from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.error import URLError
from urllib.parse import urlencode

import pytest
from flask import session

from app import create_app, spotify_auth
from app import storage
from app.routes import pages
from app.services import mb_client, mb_queue, recording_matching, spotify_catalogue


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    monkeypatch.setattr(pages, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    return app


def login(client, user):
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = user
        sess["csrf_token"] = "test-csrf"


def test_isrc_match_genres_origin_and_account_isolation(app, monkeypatch):
    calls = []

    def fake_request(path, params=None):
        calls.append(path)
        if path.startswith("isrc/"):
            return {"recordings": [{"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "title": "Song", "length": 100000,
                                    "artist-credit": [{"artist": {"name": "Artist"}}]}]}
        return {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "title": "Song", "first-release-date": "2020-01-01",
                "genres": [{"name": "Rock"}]}

    monkeypatch.setattr(mb_client, "request", fake_request)
    item = {"type": "track", "exportable": True, "name": "Song", "spotify_id": "track1", "isrc": "USAAA1234567",
            "duration_ms": 100000, "artists": [{"id": "artist1", "name": "Artist"}]}
    with app.app_context():
        mb_queue.enqueue_playlist("alice", [item])
        # Process the recording first to avoid involving artist search in this assertion.
        from app import storage
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs WHERE kind='artist'")
        assert mb_queue.process_one()
        match = mb_queue.get_match("alice", "recording", "track1")
        assert match["status"] == "confirmed"
        assert match["enriched"]["first_release_date"] == "2020-01-01"
        assert mb_queue.get_match("bob", "recording", "track1") is None
        merged = mb_queue.merged_genres(
            [{"name": "rock", "source": "spotify", "level": "artist", "external_id": "artist1"}],
            match["enriched"]["genres"],
        )
        assert len(merged) == 1
        assert {origin["source"] for origin in merged[0]["origins"]} == {"spotify", "musicbrainz"}
        assert "url" in calls
        assert "isrc/USAAA1234567" in calls


def test_homonyms_manual_override_and_csrf(app, monkeypatch):
    candidates = [
        {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "Même nom"},
        {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "name": "Même nom"},
    ]

    def fake_request(path, params=None):
        if path == "artist":
            return {"artists": candidates}
        return {"id": path.split("/")[-1], "name": "Même nom", "type": "Person",
                "begin-area": {"id": "area-1", "name": "Paris"}, "genres": []}

    monkeypatch.setattr(mb_client, "request", fake_request)
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "spotify-one", {"name": "Même nom"})
        mb_queue.enqueue("bob", "artist", "spotify-one", {"name": "Même nom"})
        mb_queue.process_one()
        mb_queue.process_one()
        assert mb_queue.get_match("alice", "artist", "spotify-one")["status"] == "ambiguous"
    client = app.test_client()
    login(client, "alice")
    url = "/musicbrainz/match/artist/spotify-one"
    assert client.post(url, json={"mbid": candidates[0]["id"]}).status_code == 403
    response = client.post(url, json={"mbid": candidates[0]["id"]}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 200
    with app.app_context():
        assert mb_queue.get_match("bob", "artist", "spotify-one")["status"] == "ambiguous"
        mb_queue.process_one()
        match = mb_queue.get_match("alice", "artist", "spotify-one")
        assert match["enriched"]["origin"]["name"] == "Paris"
        assert mb_queue.status_for("alice")["artists_with_origin"] == 1
        assert mb_queue.get_match("alice", "artist", "spotify-one")["mbid"] == candidates[0]["id"]
    corrected = client.post(url, json={"mbid": candidates[1]["id"]}, headers={"X-CSRF-Token": "test-csrf"})
    assert corrected.json["mbid"] == candidates[1]["id"]
    with app.app_context():
        mb_queue.process_one()
        assert mb_queue.get_match("alice", "artist", "spotify-one")["enriched"]["external_id"] == candidates[1]["id"]
    assert client.post(url, json={"mbid": None}, headers={"X-CSRF-Token": "test-csrf"}).json["status"] == "not_found"


def test_artist_autoconfirm_requires_spotify_relation(app, monkeypatch):
    mbid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    def fake_request(path, params=None):
        if path == "artist":
            return {"artists": [{"id": mbid, "name": "Unique"}]}
        if path == "url":
            return {"urls": [{"resource": "https://open.spotify.com/artist/spotify-one",
                              "relations": [{"artist": {"id": mbid}}]}]}
        return {"id": mbid, "name": "Unique", "genres": []}

    monkeypatch.setattr(mb_client, "request", fake_request)
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Unique"})
        mb_queue.process_one()
        assert mb_queue.get_match("alice", "artist", "spotify-one")["status"] == "confirmed"
        assert mb_queue.get_match("alice", "artist", "spotify-one")["match_method"] == "spotify_url"


def test_batch_url_recheck_and_manual_choice_preserved(app, monkeypatch):
    first = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    second = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        if path == "url":
            return {"urls": [{"resource": f"https://open.spotify.com/artist/{artist}",
                              "relations": [{"artist": {"id": first}}]}
                             for artist in ("spotify-one", "spotify-two")]}
        return {"id": path.split("/")[-1] if path.startswith("artist/") else first, "name": "Name", "genres": []}

    monkeypatch.setattr(mb_client, "request", fake_request)
    with app.app_context():
        for artist in ("spotify-one", "spotify-two", "spotify-manual"):
            mb_queue.enqueue("alice", "artist", artist, {"id": artist, "name": "Name"})
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
            db.execute("UPDATE mb_matches SET status='ambiguous',candidates=?",
                       (mb_client._json([{"id": first, "name": "Name"}, {"id": second, "name": "Name"}]),))
        assert mb_queue.set_manual("alice", "artist", "spotify-manual", second)
        mb_queue.enqueue_rechecks()
        mb_queue.process_one()
        assert mb_queue.get_match("alice", "artist", "spotify-one")["mbid"] == first
        assert mb_queue.get_match("alice", "artist", "spotify-two")["mbid"] == first
        assert mb_queue.get_match("alice", "artist", "spotify-manual")["mbid"] == second
        assert mb_queue.get_match("alice", "artist", "spotify-manual")["match_method"] == "manual"
        assert len([call for call in calls if call[0] == "url"]) == 1


def test_two_album_titles_alone_no_longer_confirm_an_artist(app, monkeypatch):
    mbid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    monkeypatch.setattr(spotify_catalogue, "_spotify_albums", lambda _id: ["Alpha", "Beta", "Beta"])

    def fake_request(path, params=None):
        if path == "url":
            return {"urls": []}
        if path == "artist":
            return {"artists": [{"id": mbid, "name": "Artist"}]}
        if path == "release-group":
            return {"release-groups": [{"title": "Alpha"}, {"title": "Béta"}]}
        return {"id": mbid, "name": "Artist", "genres": []}

    monkeypatch.setattr(mb_client, "request", fake_request)
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Artist"})
        mb_queue.process_one()
        match = mb_queue.get_match("alice", "artist", "spotify-one")
        assert match["status"] == "ambiguous"
        assert match["mbid"] is None
        assert match["match_evidence"]["ranked"][0]["recording_count"] == 0


def test_album_fallback_rejects_weak_or_homonym_evidence(app, monkeypatch):
    first = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    second = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    monkeypatch.setattr(recording_matching, "_direct_links", lambda ids: {key: set() for key in ids})
    monkeypatch.setattr(spotify_catalogue, "album_comparison", lambda spotify_id, mbid: {
        "common_albums": ["Alpha", "Alpha"], "spotify_albums": [], "musicbrainz_albums": []})
    monkeypatch.setattr(mb_client, "request", lambda path, params=None: {
        "artists": [{"id": first, "name": "Name"}, {"id": second, "name": "Name"}]})
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Name"})
        mb_queue.process_one()
        assert mb_queue.get_match("alice", "artist", "spotify-one")["status"] == "ambiguous"
        monkeypatch.setattr(mb_client, "request", lambda path, params=None: {
            "artists": [{"id": first, "name": "Name"}]})
        mb_queue.enqueue("alice", "artist", "spotify-two", {"id": "spotify-two", "name": "Name"})
        mb_queue.process_one()
        assert mb_queue.get_match("alice", "artist", "spotify-two")["status"] == "ambiguous"


def test_multiple_direct_links_do_not_autoconfirm(app, monkeypatch):
    first = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    second = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    monkeypatch.setattr(recording_matching, "_direct_links", lambda ids: {key: {first, second} for key in ids})
    monkeypatch.setattr(spotify_catalogue, "album_comparison", lambda spotify_id, mbid: {
        "common_albums": [], "spotify_albums": [], "musicbrainz_albums": []})
    monkeypatch.setattr(mb_client, "request", lambda path, params=None: {
        "artists": [{"id": first, "name": "Name"}, {"id": second, "name": "Name"}]})
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Name"})
        mb_queue.process_one()
        assert mb_queue.get_match("alice", "artist", "spotify-one")["status"] == "ambiguous"


def test_comparison_is_account_scoped(app, monkeypatch):
    mbid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Name"})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET candidates=? WHERE user_id='alice'",
                       (mb_client._json([{"id": mbid, "name": "Name"}]),))
    monkeypatch.setattr(spotify_catalogue, "album_comparison", lambda spotify_id, candidate: {
        "spotify_albums": ["Alpha"], "musicbrainz_albums": ["Alpha"], "common_albums": ["Alpha"]})
    client = app.test_client()
    login(client, "bob")
    url = f"/musicbrainz/comparison/artist/spotify-one/{mbid}"
    assert client.get(url).status_code == 404
    login(client, "alice")
    assert client.get(url).json["common_albums"] == ["Alpha"]
    assert client.get("/musicbrainz/comparison/artist/spotify-one/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb").status_code == 404


def test_missing_isrc_no_result_failure_and_resume(app, monkeypatch):
    searched = []

    def no_result(path, params=None):
        searched.append((path, params))
        return {"recordings": []}

    monkeypatch.setattr(mb_client, "request", no_result)
    with app.app_context():
        mb_queue.enqueue("alice", "recording", "song", {"name": "Song", "artists": [{"name": "Artist"}]})
        mb_queue.process_one()
        assert any(path == "recording" for path, _ in searched)
        assert mb_queue.get_match("alice", "recording", "song")["status"] == "not_found"
        mb_queue.enqueue("alice", "artist", "artist", {"name": "Artist"})

    def failed(path, params=None):
        raise mb_client.TemporaryFailure("offline")

    monkeypatch.setattr(mb_client, "request", failed)
    with app.app_context():
        mb_queue.process_one()
        assert mb_queue.status_for("alice")["queued"] == 1
        with mb_client.storage.connect() as db:
            db.execute("UPDATE mb_jobs SET available_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    # A new app instance uses the same SQLite database and resumes the delayed job.
    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "test"})
    monkeypatch.setattr(mb_client, "request", lambda path, params=None: {"artists": []})
    with restarted.app_context():
        mb_queue.process_one()
        assert mb_queue.get_match("alice", "artist", "artist")["status"] == "not_found"
        assert mb_queue.status_for("alice")["queued"] == 0


def _recording_fixture():
    payload = {"id": "track-one", "name": "Song", "artists": [{"name": "Singer"}],
               "album_name": "Shared Release", "release_date": "2020-06-01",
               "duration_ms": 180000, "isrc": "USAAA1234567"}
    candidates = [
        {"id": "recording-one", "name": "Song", "artist_names": ["Singer"],
         "length": 180000, "isrcs": ["USAAA1234567"]},
        {"id": "recording-two", "name": "Song", "artist_names": ["Singer"],
         "length": 180000, "isrcs": []},
    ]
    linked = {"recordings": set(), "releases": set(), "groups": set()}
    return payload, candidates, linked


def test_recording_url_confirms_only_matching_recording(app, monkeypatch):
    payload, candidates, linked = _recording_fixture()
    payload["album_id"] = "album-one"
    with app.app_context():
        mb_queue.enqueue("alice", "recording", payload["id"], payload)
        monkeypatch.setattr(mb_client, "request", lambda path, params=None: {
            "urls": [{"resource": "https://open.spotify.com/track/track-one",
                      "relations": [{"recording": {"id": "recording-one"}}]},
                     {"resource": "https://open.spotify.com/album/album-one",
                      "relations": [{"release": {"id": "release-one"}}]}]})
        links = recording_matching._recording_links([{"user_id": "alice", "spotify_id": "track-one"}])
        assert links[("alice", "track-one")]["recordings"] == {"recording-one"}
        assert links[("alice", "track-one")]["releases"] == {"release-one"}
        selected = recording_matching._select_recording(payload, candidates, links[("alice", "track-one")])
        assert selected[:2] == ("recording-one", "spotify_url")
        linked["recordings"] = {"recording-two", "recording-three"}
        monkeypatch.setattr(recording_matching, "_release_context", lambda _id: [])
        assert recording_matching._select_recording(payload, candidates, linked)[:2] == ("recording-two", "disambiguation")


def test_multiple_recording_links_use_evidence_and_ignore_wrong_relation(app, monkeypatch):
    payload, candidates, _ = _recording_fixture()
    with app.app_context():
        mb_queue.enqueue("alice", "recording", payload["id"], payload)
        monkeypatch.setattr(mb_client, "request", lambda path, params=None: {
            "urls": [{"resource": "https://open.spotify.com/track/track-one",
                      "relations": [{"recording": {"id": "recording-one"}},
                                    {"recording": {"id": "recording-two"}},
                                    {"artist": {"id": "wrong-entity"}}]}]})
        links = recording_matching._recording_links([{"user_id": "alice", "spotify_id": "track-one"}])[("alice", "track-one")]
        assert links["recordings"] == {"recording-one", "recording-two"}
        monkeypatch.setattr(recording_matching, "_release_context", lambda _id: [])
        assert recording_matching._select_recording(payload, candidates, links)[:2] == ("recording-one", "disambiguation")


def test_isrc_without_results_falls_back_to_name_search(app, monkeypatch):
    calls = []

    def fake_request(path, params=None):
        calls.append(path)
        if path.startswith("isrc/"):
            return {"recordings": []}
        return {"recordings": [{"id": "recording-one", "title": "Song", "length": 180000,
                                "artist-credit": [{"artist": {"name": "Singer"}}]}]}

    monkeypatch.setattr(mb_client, "request", fake_request)
    payload, _, _ = _recording_fixture()
    with app.app_context():
        assert recording_matching._recording_candidates(payload)[0]["id"] == "recording-one"
    assert calls == ["isrc/USAAA1234567", "recording"]


@pytest.mark.parametrize("kind", ["album", "ep"])
def test_recording_release_evidence_requires_membership(app, monkeypatch, kind):
    payload, candidates, linked = _recording_fixture()
    release = {"id": "release-one", "title": "Shared Release", "date": "2020-06-01",
               "group_id": "group-one", "group_type": kind, "artist_names": ["Singer"]}
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: [release] if mbid == "recording-one" else [])
    with app.app_context():
        selected = recording_matching._select_recording(payload, candidates, linked)
        assert selected[:2] == ("recording-one", "disambiguation")
        assert selected[2]["release_type"] == kind
        monkeypatch.setattr(recording_matching, "_release_context", lambda _id: [])
        assert "release_id" not in recording_matching._select_recording(payload, candidates, linked)[2]


def test_release_context_reads_recording_membership_and_group_type(app, monkeypatch):
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        return {"release-count": 1, "releases": [{"id": "release-one", "title": "Shared Release",
                "date": "2020-06-01", "artist-credit": [{"artist": {"name": "Singer"}}],
                "release-group": {"id": "group-one", "primary-type": "EP"}}]}

    monkeypatch.setattr(mb_client, "request", fake_request)
    with app.app_context():
        context = recording_matching._release_context("recording-one")
    # Fewer releases than the lookup limit: the shared recording lookup is complete, no browse needed.
    assert calls == [("recording/recording-one", {"inc": mb_client.RECORDING_INC})]
    assert context[0]["group_type"] == "ep"
    assert context[0]["artist_names"] == ["Singer"]


def test_album_url_is_evidence_only_and_contribution_fallback(app, monkeypatch):
    payload, candidates, linked = _recording_fixture()
    payload["album_name"] = "Unrelated Spotify Label"
    linked["releases"] = {"release-one"}
    release = {"id": "release-one", "title": "Different Title", "date": "2020-06-01",
               "group_id": "group-one", "group_type": "single", "artist_names": ["Other Singer"]}
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: [release] if mbid == "recording-one" else [])
    with app.app_context():
        selected = recording_matching._select_recording(payload, candidates, linked)
        assert selected[:2] == ("recording-one", "disambiguation")
        assert selected[2]["criteria"]["spotify_album_url"]
        monkeypatch.setattr(recording_matching, "_release_context", lambda _id: [])
        assert recording_matching._select_recording(payload, candidates, linked)[1] != "spotify_url"


def test_contribution_recognizes_featured_artist_on_main_artist_release(app, monkeypatch):
    payload, candidates, linked = _recording_fixture()
    payload["artists"].append({"name": "Guest"})
    candidates[0]["artist_names"] = ["Singer", "Guest"]
    release = {"id": "release-one", "title": "Shared Release", "date": "2020-06-01",
               "group_id": "group-one", "group_type": "single", "artist_names": ["Singer"]}
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: [release] if mbid == "recording-one" else [])
    with app.app_context():
        assert recording_matching._select_recording(payload, candidates, linked)[:2] == ("recording-one", "disambiguation")


def test_recording_disambiguation_tie_and_duration_conflict(app, monkeypatch):
    payload, candidates, linked = _recording_fixture()
    release = {"id": "release-one", "title": "Shared Release", "date": "2020-06-01",
               "group_id": "group-one", "group_type": "single", "artist_names": ["Singer"]}
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: [release] if mbid == "recording-one" else [])
    with app.app_context():
        selected = recording_matching._select_recording(payload, candidates, linked)
        assert selected[:2] == ("recording-one", "disambiguation")
        assert selected[2]["selected_score"] == 100
        monkeypatch.setattr(recording_matching, "_release_context", lambda _id: [release])
        candidates[1]["isrcs"] = [payload["isrc"]]
        assert recording_matching._select_recording(payload, candidates, linked)[:2] == ("recording-one", "first_candidate")
        candidates[0]["length"] = 210000
        candidates[1]["length"] = 210000
        assert recording_matching._select_recording(payload, candidates, linked)[0] is None


def _bother_fixture():
    payload = {"id": "bother", "name": "Bother", "artists": [{"name": "Stone Sour"}],
               "album_name": "Stone Sour", "album_type": "album", "release_date": "2002-08-27",
               "duration_ms": 240000, "isrc": "USRR10200008"}
    candidates = [{"id": recording_id, "name": "Bother", "artist_names": ["Stone Sour"],
                   "length": 240000, "isrcs": []} for recording_id in ("z-first", "a-second")]
    releases = {item["id"]: [{"id": f"release-{index}", "title": "Stone Sour",
                              "date": "2002-08-27", "group_id": f"group-{index}",
                              "group_type": "album", "artist_names": ["Stone Sour"]}]
                for index, item in enumerate(candidates)}
    linked = {"recordings": set(), "releases": set(), "groups": set()}
    return payload, candidates, releases, linked


@pytest.mark.parametrize("criterion", ["release_url", "group_url", "isrc", "title", "date", "duration", "type"])
def test_bother_disambiguates_by_ordered_evidence(app, monkeypatch, criterion):
    payload, candidates, releases, linked = _bother_fixture()
    first, second = candidates
    first_release, second_release = releases[first["id"]][0], releases[second["id"]][0]
    if criterion in ("release_url", "group_url"):
        linked["releases" if criterion == "release_url" else "groups"].add(
            second_release["id" if criterion == "release_url" else "group_id"])
        first["isrcs"] = [payload["isrc"]]  # Album identity outranks ISRC.
    elif criterion == "isrc":
        second["isrcs"] = ["US-RR1-02-00008"]
        second_release["title"] = "Another album"  # ISRC outranks the title.
    elif criterion == "title":
        first_release["title"] = "Another album"
        second_release["date"] = "2020-01-01"  # Title outranks date.
    elif criterion == "date":
        first_release["date"] = "2002-09-01"
        second["length"] += 4000  # Date outranks duration, even within the same year.
    elif criterion == "duration":
        first["length"] += 1000
        second_release["group_type"] = "single"  # Duration outranks type.
    else:
        first_release["group_type"] = "single"
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: releases[mbid])
    with app.app_context():
        selected = recording_matching._select_recording(payload, candidates, linked)
        assert selected[:2] == (second["id"], "disambiguation")
        assert selected[2]["ranked"][0]["id"] == second["id"]
        assert selected[2]["selection"] == "disambiguation"


def test_bother_uses_best_edition_independently_of_release_order(app, monkeypatch):
    payload, candidates, releases, linked = _bother_fixture()
    first_release = releases[candidates[0]["id"]][0]
    first_release["date"] = "2002-09-01"
    later_edition = {**first_release, "id": "later-edition", "date": "2020-01-01"}
    exact_edition = {**first_release, "id": "exact-edition", "date": payload["release_date"]}
    releases[candidates[1]["id"]][0]["date"] = "2002-08-01"
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: releases[mbid])
    with app.app_context():
        for editions in ([later_edition, exact_edition], [exact_edition, later_edition]):
            releases[candidates[0]["id"]] = editions
            selected = recording_matching._select_recording(payload, candidates, linked)
            assert selected[:2] == (candidates[0]["id"], "disambiguation")
            assert selected[2]["release_id"] == "exact-edition"
            assert recording_matching._recording_score(payload, candidates[0], editions, linked)["score"] == 90


def test_bother_tie_uses_first_candidate_and_deduplicates_recordings(app, monkeypatch):
    payload, candidates, releases, linked = _bother_fixture()
    calls = []

    def context(mbid):
        calls.append(mbid)
        return releases[mbid] * 2

    monkeypatch.setattr(recording_matching, "_release_context", context)
    with app.app_context():
        for ordered in (candidates, candidates[::-1]):
            selected = recording_matching._select_recording(payload, [*ordered, ordered[0]], linked)
            assert selected[:2] == (ordered[0]["id"], "first_candidate")
            assert selected[2]["tied_candidates"] == [item["id"] for item in ordered]
            assert len(selected[3]) == 2
        assert calls == ["z-first", "a-second", "a-second", "z-first"]


def test_bother_missing_metadata_still_uses_first_compatible_candidate(app, monkeypatch):
    payload, candidates, _, linked = _bother_fixture()
    for field in ("album_name", "release_date", "duration_ms", "isrc"):
        payload.pop(field)
    monkeypatch.setattr(recording_matching, "_release_context", lambda *_: pytest.fail("No album context to look up"))
    with app.app_context():
        selected = recording_matching._select_recording(payload, candidates, linked)
        assert selected[:2] == ("z-first", "first_candidate")
        assert selected[2]["criteria"]["duration_delta_ms"] is None
        assert "Infinity" not in mb_client._json(selected[2])


@pytest.mark.parametrize("date", ["2002", "2002-08", ""])
def test_bother_partial_or_missing_dates_do_not_invent_precision(app, monkeypatch, date):
    payload, candidates, releases, linked = _bother_fixture()
    payload["release_date"] = date
    releases[candidates[1]["id"]][0]["date"] = "2002-08-01"
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: releases[mbid])
    with app.app_context():
        selected = recording_matching._select_recording(payload, candidates, linked)
        assert selected[:2] == (candidates[0]["id"], "first_candidate")


@pytest.mark.parametrize("conflict", ["artist", "title", "duration", "recording_url"])
def test_bother_fallback_rejects_incompatible_recordings(app, monkeypatch, conflict):
    payload, candidates, releases, linked = _bother_fixture()
    for item in candidates:
        if conflict == "artist":
            item["artist_names"] = ["Other artist"]
        elif conflict == "title":
            item["name"] = "Bother live"
        elif conflict == "duration":
            item["length"] += 15001
    if conflict == "recording_url":
        linked["recordings"] = {"other-recording"}
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: releases[mbid])
    monkeypatch.setattr(mb_client, "request", lambda *_args, **_kwargs: {})
    with app.app_context():
        selected = recording_matching._select_recording(payload, candidates, linked)
        assert selected[0] is None
        assert selected[1] is None


def test_release_context_paginates_all_editions(app, monkeypatch):
    offsets = []

    def request(path, params):
        if path == "recording/bother":
            # A full lookup page may be truncated, so every edition is browsed instead.
            return {"releases": [{"id": f"lookup-{index}"} for index in range(mb_client.LOOKUP_LIMIT)]}
        offsets.append(params["offset"])
        return {"release-count": 2, "releases": [{"id": str(params["offset"]), "title": "Stone Sour",
                "release-group": {"id": "shared-group", "primary-type": "Album"}}]}

    monkeypatch.setattr(mb_client, "request", request)
    with app.app_context():
        assert [item["id"] for item in recording_matching._release_context("bother")] == ["0", "1"]
    assert offsets == [0, 1]


def test_recording_details_reuse_the_release_context_lookup(app, monkeypatch):
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        return {"id": "recording-one", "title": "Song", "first-release-date": "2020", "releases": []}

    monkeypatch.setattr(mb_client, "request", fake_request)
    with app.app_context():
        recording_matching._release_context("recording-one")
        mb_client._details("recording", "recording-one")
    assert calls[0] == calls[1]


def test_version_three_rechecks_once_and_preserves_existing_decisions(app, monkeypatch):
    payload, candidates, releases, linked = _bother_fixture()
    monkeypatch.setattr(recording_matching, "_recording_links", lambda *_: {})
    monkeypatch.setattr(recording_matching, "_recording_candidates", lambda data: candidates if data["id"] == "old" else [
        {**candidates[0], "artist_names": ["Other artist"]}])
    monkeypatch.setattr(recording_matching, "_release_context", lambda mbid: releases[mbid])
    monkeypatch.setattr(mb_client, "_details", lambda _kind, mbid: {"external_id": mbid})
    with app.app_context():
        for spotify_id in ("old", "still-ambiguous", "current", "confirmed", "manual", "rejected"):
            mb_queue.enqueue("alice", "recording", spotify_id, {**payload, "id": spotify_id})
        assert mb_queue.set_manual("alice", "recording", "manual", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        assert mb_queue.set_manual("alice", "recording", "rejected", None)
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=2 WHERE manual=0")
            db.execute("UPDATE mb_matches SET match_version=3 WHERE spotify_id='current'")
            db.execute("UPDATE mb_matches SET status='confirmed',mbid='kept' WHERE spotify_id='confirmed'")
            db.execute("DELETE FROM mb_jobs")
        preserved = {key: mb_queue.get_match("alice", "recording", key)
                     for key in ("current", "confirmed", "manual", "rejected")}
        assert mb_queue.progress_for("alice")["completed"] == 4
        assert mb_queue.progress_for("alice")["reviews"] == 1
        mb_queue.enqueue_rechecks()
        mb_queue.enqueue_rechecks()
        assert mb_queue.status_for("alice")["queued"] == 2
        assert mb_queue.process_one()
        assert not mb_queue.process_one()
        old = mb_queue.get_match("alice", "recording", "old")
        assert old["status"] == "confirmed"
        assert old["match_method"] == "first_candidate"
        assert old["match_evidence"]["tied_candidates"] == ["z-first", "a-second"]
        assert mb_queue.get_match("alice", "recording", "still-ambiguous")["status"] == "ambiguous"
        with storage.connect() as db:
            assert db.execute("SELECT match_version FROM mb_matches WHERE spotify_id='old'").fetchone()[0] == 3
        mb_queue.enqueue_rechecks()
        assert not mb_queue.process_one()
        for key, original in preserved.items():
            assert mb_queue.get_match("alice", "recording", key) == original


def test_recheck_backfills_recording_and_preserves_manual_choice(app):
    item = {"spotify_id": "track-one", "album_name": "Shared Release", "album_id": "album-one",
            "album_type": "single", "release_date": "2020-06-01"}
    with app.app_context():
        storage.replace_playlist_items("alice", "playlist", [item])
        mb_queue.enqueue("alice", "recording", "track-one", {"name": "Song", "artists": [{"name": "Singer"}]})
        mb_queue.enqueue("alice", "recording", "manual-track", {"name": "Manual"})
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=1")
        assert mb_queue.set_manual("alice", "recording", "manual-track", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
        mb_queue.enqueue_rechecks()
        match = mb_queue.get_match("alice", "recording", "track-one")
        assert match["spotify"]["album_id"] == "album-one"
        assert mb_queue.status_for("alice")["queued"] == 1
        assert mb_queue.status_for("alice")["recordings"]["ambiguous"] == 1
        assert mb_queue.get_match("alice", "recording", "manual-track")["match_method"] == "manual"


def test_playlist_enqueue_keeps_release_metadata(app):
    item = {"type": "track", "exportable": True, "spotify_id": "track-one", "name": "Song",
            "artists": [{"id": "artist-one", "name": "Singer"}], "album_name": "An EP",
            "album_id": "album-one", "album_type": "single", "release_date": "2020-06"}
    with app.app_context():
        mb_queue.enqueue_playlist("alice", [item])
        stored = mb_queue.get_match("alice", "recording", "track-one")["spotify"]
        assert (stored["album_name"], stored["album_id"], stored["release_date"]) == (
            "An EP", "album-one", "2020-06")


def test_progress_page_uses_completed_jobs_and_is_account_scoped(app):
    client = app.test_client()
    assert client.get("/progress").status_code == 302
    assert client.get("/musicbrainz/progress").status_code == 401
    with app.app_context():
        for spotify_id in ("done", "reviewed", "old-review", "pending"):
            mb_queue.enqueue("alice", "recording", spotify_id, {"name": spotify_id})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET status='confirmed',match_version=2 WHERE spotify_id='done'")
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=3 WHERE spotify_id='reviewed'")
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=2 WHERE spotify_id='old-review'")
            db.execute("DELETE FROM mb_jobs WHERE spotify_id IN ('done','reviewed')")
    login(client, "alice")
    page = client.get("/progress")
    assert page.status_code == 200
    assert b'<progress id="recording-progress"' in page.data
    assert client.get("/musicbrainz/progress").json == {
        "total": 4, "completed": 2, "confirmed": 1, "reviews": 1, "not_found": 0, "queued": 2, "deferred": 0,
        "errors": {"attempts": 0, "failed": 0, "since": None, "percent": None}}
    login(client, "bob")
    assert client.get("/musicbrainz/progress").json["total"] == 0


def test_repeatedly_incomplete_artist_is_deferred_daily_and_counted_apart(app, monkeypatch):
    from app.services import artist_matching
    monkeypatch.setattr(artist_matching, "analyze", lambda *args: (None, None, {"complete": False}, [{"id": "candidate"}]))
    monkeypatch.setattr(recording_matching, "_artist_candidates", lambda payload: [])
    with app.app_context():
        for spotify_id in ("early", "stuck"):
            mb_queue.enqueue("alice", "artist", spotify_id, {"id": spotify_id, "name": spotify_id})
        with storage.connect() as db:
            db.execute("UPDATE mb_jobs SET attempts=?", (mb_queue.DEFERRED_AFTER - 1,))
            db.execute("UPDATE mb_jobs SET attempts=0 WHERE spotify_id='early'")
            jobs = [dict(row) for row in db.execute("SELECT * FROM mb_jobs")]
        for job in jobs:
            mb_queue._process_job(job)
        with storage.connect() as db:
            rows = {row["spotify_id"]: dict(row) for row in db.execute("SELECT * FROM mb_jobs")}
        assert rows["early"]["state"] == "queued"
        assert rows["stuck"]["state"] == "deferred" and rows["stuck"]["attempts"] == mb_queue.DEFERRED_AFTER
        delay = datetime.fromisoformat(rows["stuck"]["available_at"]) - datetime.now(timezone.utc)
        assert timedelta(hours=23) < delay <= mb_queue.DEFERRED_DELAY
        progress = mb_queue.progress_for("alice", "artist")
        assert (progress["queued"], progress["deferred"], progress["completed"]) == (1, 1, 0)
        with storage.connect() as db:
            db.execute("UPDATE mb_jobs SET available_at=? WHERE spotify_id='stuck'", (storage.now(),))
            db.execute("DELETE FROM mb_jobs WHERE spotify_id='early'")
        assert mb_queue.claim_job()["spotify_id"] == "stuck"


def test_progress_and_reviews_are_separate_for_artists_and_tracks(app):
    with app.app_context():
        for kind, prefix, count in (("artist", "artist", 22), ("recording", "track", 3)):
            for index in range(count):
                mb_queue.enqueue("alice", kind, f"{prefix}-{index:02}", {"name": f"{prefix}-{index:02}"})
        mb_queue.enqueue("bob", "artist", "private", {"name": "Private"})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=2 "
                       "WHERE user_id='alice' AND kind='artist'")
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=3 "
                       "WHERE user_id='alice' AND kind='recording'")
            db.execute("DELETE FROM mb_jobs WHERE user_id='alice'")
    client = app.test_client()
    login(client, "alice")
    assert client.get("/musicbrainz/progress?kind=artist").json == {
        "total": 22, "completed": 22, "confirmed": 0, "reviews": 22, "not_found": 0, "queued": 0, "deferred": 0,
        "errors": {"attempts": 0, "failed": 0, "since": None, "percent": None}}
    assert client.get("/musicbrainz/progress?kind=recording").json["reviews"] == 3
    assert client.get("/musicbrainz/progress").json["total"] == 3
    first = client.get("/musicbrainz/reviews?kind=artist").json["reviews"]
    second = client.get("/musicbrainz/reviews?kind=artist&offset=20").json["reviews"]
    tracks = client.get("/musicbrainz/reviews?kind=recording").json["reviews"]
    assert len(first) == 20 and len(second) == 2 and len(tracks) == 3
    assert {item["kind"] for item in first + second} == {"artist"}
    assert {item["kind"] for item in tracks} == {"recording"}
    assert len(client.get("/musicbrainz/reviews").json["reviews"]) == 20
    assert client.get("/musicbrainz/progress?kind=other").status_code == 400
    assert client.get("/musicbrainz/reviews?kind=other").status_code == 400
    login(client, "bob")
    assert client.get("/musicbrainz/progress?kind=artist").json["total"] == 1
    assert client.get("/musicbrainz/reviews?kind=artist").json["reviews"] == []


def test_global_rate_limit_and_http_failure(app, monkeypatch):
    times = iter([100.0, 100.0, 101.1])
    sleeps = []
    monkeypatch.setattr(mb_client.time, "time", lambda: next(times))
    monkeypatch.setattr(mb_client.time, "sleep", lambda seconds: sleeps.append(seconds))
    with app.app_context():
        mb_client._rate_slot()
        mb_client._rate_slot()
    assert sleeps == [pytest.approx(1.1)]

    def offline(_req, timeout):
        raise URLError("offline")

    monkeypatch.setattr(mb_client, "urlopen", offline)
    monkeypatch.setattr(mb_client, "_rate_slot", lambda: None)
    with app.app_context(), pytest.raises(mb_client.TemporaryFailure):
        mb_client.request("artist", {"query": "test"})


def test_confirmed_enrichment_is_kept_without_refresh(app):
    mbid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
            db.execute("UPDATE mb_matches SET status='confirmed',mbid=?,enriched=?,updated_at=?",
                       (mbid, mb_client._json({"source": "musicbrainz", "external_id": mbid, "name": "Artist"}),
                        (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()))
        mb_queue.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        assert mb_queue.status_for("alice")["queued"] == 0
        assert mb_queue.get_match("alice", "artist", "artist1")["enriched"]["name"] == "Artist"


def test_unmatched_entity_is_retried_after_seven_days(app):
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
            db.execute("UPDATE mb_matches SET status='not_found',updated_at=?",
                       ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),))
        mb_queue.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        assert mb_queue.status_for("alice")["queued"] == 1


def test_empty_http_result_expires_after_seven_days(app, monkeypatch):
    key = "artist?" + urlencode(sorted({"query": "Artist", "fmt": "json"}.items()))
    with app.app_context():
        with storage.connect() as db:
            db.execute("INSERT INTO mb_http_cache VALUES(?,?,?)",
                       (key, mb_client._json({"artists": []}),
                        (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()))
        monkeypatch.setattr(mb_client, "_rate_slot", lambda: None)
        monkeypatch.setattr(mb_client, "urlopen", lambda request, timeout: BytesIO(b'{"artists":[{"id":"new"}]}'))
        assert mb_client.request("artist", {"query": "Artist"})["artists"][0]["id"] == "new"


def test_graph_and_pages_expose_provenance(app):
    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": "artist1", "name": "Artist", "genres": ["Rock"]}])
        mb_queue.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET status='confirmed',mbid=?,enriched=? WHERE user_id='alice'",
                       ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", mb_client._json({
                           "source": "musicbrainz", "external_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                           "genres": [{"name": "rock", "source": "musicbrainz", "level": "artist",
                                       "external_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}],
                       })))
    client = app.test_client()
    login(client, "alice")
    graph = client.get("/graphs/get_artist_genre_graph_data").json
    assert len(graph["edges"]) == 1
    assert {origin["source"] for origin in graph["edges"][0]["origins"]} == {"spotify", "musicbrainz"}
    assert b'mb-panel' not in client.get("/graphs").data
    assert b'csrf-token' in client.get("/graphs").data
    assert b'mb-panel' not in client.get("/sorts").data
    progress = client.get("/progress").data
    assert b'id="tab-recording"' in progress
    assert b'id="tab-artist"' in progress
    assert b'role="tablist"' in progress
    assert b'aria-controls="panel-recording" aria-selected="true"' in progress
    assert b'aria-controls="panel-artist" aria-selected="false"' in progress
    assert b'id="panel-artist" role="tabpanel" aria-labelledby="tab-artist" tabindex="0" hidden' in progress
    assert progress.count(b'class="surface mb-panel"') == 2


@pytest.mark.parametrize("kind", ["artist", "recording"])
@pytest.mark.parametrize("stage", ["lookup", "analysis"])
def test_error_rate_counts_failed_attempts_retries_and_survives_restart(app, monkeypatch, kind, stage):
    def unavailable(*_args, **_kwargs):
        raise mb_client.TemporaryFailure("MusicBrainz HTTP 503")

    lookup = "_direct_links" if kind == "artist" else "_recording_links"
    candidates = "_artist_candidates" if kind == "artist" else "_recording_candidates"
    monkeypatch.setattr(recording_matching, lookup, unavailable if stage == "lookup" else lambda *_: {})
    monkeypatch.setattr(recording_matching, candidates, unavailable if stage == "analysis" else lambda *_: [])
    with app.app_context():
        assert mb_queue.progress_for("alice", kind)["errors"]["percent"] is None
        mb_queue.enqueue("alice", kind, "retry", {"name": "Retry"})
        assert mb_queue.process_one()
        errors = mb_queue.progress_for("alice", kind)["errors"]
        assert errors["attempts"] == 1
        assert errors["failed"] == 1
        assert errors["percent"] == 100
        assert mb_queue.progress_for("alice", kind)["completed"] == 0
        since = errors["since"]
        with storage.connect() as db:
            db.execute("UPDATE mb_jobs SET available_at=?", (storage.now(),))
    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "test"})
    monkeypatch.setattr(recording_matching, lookup, lambda *_: {})
    monkeypatch.setattr(recording_matching, candidates, lambda *_: [])
    with restarted.app_context():
        assert mb_queue.process_one()
        data = mb_queue.progress_for("alice", kind)
        assert data["not_found"] == 1  # Empty results are successful analyses.
        assert data["completed"] == 1
        assert data["errors"] == {"attempts": 2, "failed": 1, "percent": 50, "since": since}
        mb_queue.enqueue("alice", kind, "second", {"name": "Second"})
        assert mb_queue.process_one()
        assert mb_queue.progress_for("alice", kind)["errors"]["percent"] == 33.3
        assert mb_queue.progress_for("bob", kind)["errors"]["attempts"] == 0
        other_kind = "recording" if kind == "artist" else "artist"
        assert mb_queue.progress_for("alice", other_kind)["errors"]["attempts"] == 0


def test_batched_lookup_failures_count_once_per_job_and_account(app, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise mb_client.TemporaryFailure("MusicBrainz HTTP 503")
    monkeypatch.setattr(recording_matching, "_direct_links", unavailable)
    with app.app_context():
        mb_queue.enqueue("alice", "artist", "one", {"name": "One"})
        mb_queue.enqueue("alice", "artist", "two", {"name": "Two"})
        mb_queue.enqueue("bob", "artist", "one", {"name": "Other One"})
        assert mb_queue.process_one()
        assert mb_queue.progress_for("alice", "artist")["errors"]["attempts"] == 2
        assert mb_queue.progress_for("alice", "artist")["errors"]["failed"] == 2
        assert mb_queue.progress_for("bob", "artist")["errors"]["failed"] == 1
        assert not mb_queue.process_one()  # Waiting for retries does not count as another failure.
        assert mb_queue.progress_for("alice", "artist")["errors"]["failed"] == 2


def test_purge_cache_removes_only_entries_no_reader_accepts(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        with storage.connect() as db:
            db.executemany("INSERT INTO mb_http_cache VALUES(?,?,?)", [
                ("old", "{}", (now - mb_client.CACHE_AGE - timedelta(hours=1)).isoformat()),
                ("recent", "{}", (now - mb_client.CACHE_AGE + timedelta(hours=1)).isoformat()),
                ("fresh", "{}", now.isoformat()),
            ])
        assert mb_client.purge_cache(batch=1) == 1
        with storage.connect() as db:
            assert {row[0] for row in db.execute("SELECT cache_key FROM mb_http_cache")} == {"recent", "fresh"}


def test_status_counts_only_confirmed_artists_with_an_origin(app):
    rows = [("with-origin", "confirmed", '{"origin": {"name": "Paris"}}'),
            ("null-origin", "confirmed", '{"origin": null}'),
            ("no-origin", "confirmed", '{"genres": []}'),
            ("not-enriched", "confirmed", None),
            ("ambiguous", "ambiguous", '{"origin": {"name": "Lyon"}}')]
    with app.app_context():
        with storage.connect() as db:
            db.executemany("INSERT INTO mb_matches(user_id,kind,spotify_id,spotify_payload,status,enriched,updated_at) "
                           "VALUES('alice','artist',?,'{}',?,?,?)", [(*row, storage.now()) for row in rows])
        assert mb_queue.status_for("alice")["artists_with_origin"] == 1
