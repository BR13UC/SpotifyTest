from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.error import URLError
from urllib.parse import urlencode

import pytest
from flask import session

from app import create_app, spotify_auth
from app import storage
from app.routes import pages
from app.services import musicbrainz as mb


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

    monkeypatch.setattr(mb, "request", fake_request)
    item = {"type": "track", "exportable": True, "name": "Song", "spotify_id": "track1", "isrc": "USAAA1234567",
            "duration_ms": 100000, "artists": [{"id": "artist1", "name": "Artist"}]}
    with app.app_context():
        mb.enqueue_playlist("alice", [item])
        # Process the recording first to avoid involving artist search in this assertion.
        from app import storage
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs WHERE kind='artist'")
        assert mb.process_one()
        match = mb.get_match("alice", "recording", "track1")
        assert match["status"] == "confirmed"
        assert match["enriched"]["first_release_date"] == "2020-01-01"
        assert mb.get_match("bob", "recording", "track1") is None
        merged = mb.merged_genres(
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

    monkeypatch.setattr(mb, "request", fake_request)
    with app.app_context():
        mb.enqueue("alice", "artist", "spotify-one", {"name": "Même nom"})
        mb.enqueue("bob", "artist", "spotify-one", {"name": "Même nom"})
        mb.process_one()
        mb.process_one()
        assert mb.get_match("alice", "artist", "spotify-one")["status"] == "ambiguous"
    client = app.test_client()
    login(client, "alice")
    url = "/musicbrainz/match/artist/spotify-one"
    assert client.post(url, json={"mbid": candidates[0]["id"]}).status_code == 403
    response = client.post(url, json={"mbid": candidates[0]["id"]}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 200
    with app.app_context():
        assert mb.get_match("bob", "artist", "spotify-one")["status"] == "ambiguous"
        mb.process_one()
        match = mb.get_match("alice", "artist", "spotify-one")
        assert match["enriched"]["origin"]["name"] == "Paris"
        assert mb.status_for("alice")["artists_with_origin"] == 1
        assert mb.get_match("alice", "artist", "spotify-one")["mbid"] == candidates[0]["id"]
    corrected = client.post(url, json={"mbid": candidates[1]["id"]}, headers={"X-CSRF-Token": "test-csrf"})
    assert corrected.json["mbid"] == candidates[1]["id"]
    with app.app_context():
        mb.process_one()
        assert mb.get_match("alice", "artist", "spotify-one")["enriched"]["external_id"] == candidates[1]["id"]
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

    monkeypatch.setattr(mb, "request", fake_request)
    with app.app_context():
        mb.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Unique"})
        mb.process_one()
        assert mb.get_match("alice", "artist", "spotify-one")["status"] == "confirmed"
        assert mb.get_match("alice", "artist", "spotify-one")["match_method"] == "spotify_url"


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
        return {"id": first, "name": "Name", "genres": []}

    monkeypatch.setattr(mb, "request", fake_request)
    with app.app_context():
        for artist in ("spotify-one", "spotify-two", "spotify-manual"):
            mb.enqueue("alice", "artist", artist, {"id": artist, "name": "Name"})
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
            db.execute("UPDATE mb_matches SET status='ambiguous',candidates=?",
                       (mb._json([{"id": first, "name": "Name"}, {"id": second, "name": "Name"}]),))
        assert mb.set_manual("alice", "artist", "spotify-manual", second)
        mb.enqueue_rechecks()
        mb.process_one()
        assert mb.get_match("alice", "artist", "spotify-one")["mbid"] == first
        assert mb.get_match("alice", "artist", "spotify-two")["mbid"] == first
        assert mb.get_match("alice", "artist", "spotify-manual")["mbid"] == second
        assert mb.get_match("alice", "artist", "spotify-manual")["match_method"] == "manual"
        assert len([call for call in calls if call[0] == "url"]) == 1


def test_album_match_needs_two_distinct_titles_and_unique_exact_name(app, monkeypatch):
    mbid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    monkeypatch.setattr(mb, "_spotify_albums", lambda _id: ["Alpha", "Beta", "Beta"])

    def fake_request(path, params=None):
        if path == "url":
            return {"urls": []}
        if path == "artist":
            return {"artists": [{"id": mbid, "name": "Artist"}]}
        if path == "release-group":
            return {"release-groups": [{"title": "Alpha"}, {"title": "Béta"}]}
        return {"id": mbid, "name": "Artist", "genres": []}

    monkeypatch.setattr(mb, "request", fake_request)
    with app.app_context():
        mb.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Artist"})
        mb.process_one()
        match = mb.get_match("alice", "artist", "spotify-one")
        assert match["status"] == "confirmed"
        assert match["match_method"] == "albums"
        assert match["match_evidence"]["common_albums"] == ["Alpha", "Beta"]


def test_album_fallback_rejects_weak_or_homonym_evidence(app, monkeypatch):
    first = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    second = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    monkeypatch.setattr(mb, "_direct_links", lambda ids: {key: set() for key in ids})
    monkeypatch.setattr(mb, "album_comparison", lambda spotify_id, mbid: {
        "common_albums": ["Alpha", "Alpha"], "spotify_albums": [], "musicbrainz_albums": []})
    monkeypatch.setattr(mb, "request", lambda path, params=None: {
        "artists": [{"id": first, "name": "Name"}, {"id": second, "name": "Name"}]})
    with app.app_context():
        mb.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Name"})
        mb.process_one()
        assert mb.get_match("alice", "artist", "spotify-one")["status"] == "ambiguous"
        monkeypatch.setattr(mb, "request", lambda path, params=None: {
            "artists": [{"id": first, "name": "Name"}]})
        mb.enqueue("alice", "artist", "spotify-two", {"id": "spotify-two", "name": "Name"})
        mb.process_one()
        assert mb.get_match("alice", "artist", "spotify-two")["status"] == "ambiguous"


def test_multiple_direct_links_do_not_autoconfirm(app, monkeypatch):
    first = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    second = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    monkeypatch.setattr(mb, "_direct_links", lambda ids: {key: {first, second} for key in ids})
    monkeypatch.setattr(mb, "album_comparison", lambda spotify_id, mbid: {
        "common_albums": [], "spotify_albums": [], "musicbrainz_albums": []})
    monkeypatch.setattr(mb, "request", lambda path, params=None: {
        "artists": [{"id": first, "name": "Name"}, {"id": second, "name": "Name"}]})
    with app.app_context():
        mb.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Name"})
        mb.process_one()
        assert mb.get_match("alice", "artist", "spotify-one")["status"] == "ambiguous"


def test_comparison_is_account_scoped(app, monkeypatch):
    mbid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with app.app_context():
        mb.enqueue("alice", "artist", "spotify-one", {"id": "spotify-one", "name": "Name"})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET candidates=? WHERE user_id='alice'",
                       (mb._json([{"id": mbid, "name": "Name"}]),))
    monkeypatch.setattr(mb, "album_comparison", lambda spotify_id, candidate: {
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

    monkeypatch.setattr(mb, "request", no_result)
    with app.app_context():
        mb.enqueue("alice", "recording", "song", {"name": "Song", "artists": [{"name": "Artist"}]})
        mb.process_one()
        assert any(path == "recording" for path, _ in searched)
        assert mb.get_match("alice", "recording", "song")["status"] == "not_found"
        mb.enqueue("alice", "artist", "artist", {"name": "Artist"})

    def failed(path, params=None):
        raise mb.TemporaryFailure("offline")

    monkeypatch.setattr(mb, "request", failed)
    with app.app_context():
        mb.process_one()
        assert mb.status_for("alice")["queued"] == 1
        with mb.storage.connect() as db:
            db.execute("UPDATE mb_jobs SET available_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    # A new app instance uses the same SQLite database and resumes the delayed job.
    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "test"})
    monkeypatch.setattr(mb, "request", lambda path, params=None: {"artists": []})
    with restarted.app_context():
        mb.process_one()
        assert mb.get_match("alice", "artist", "artist")["status"] == "not_found"
        assert mb.status_for("alice")["queued"] == 0


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
        mb.enqueue("alice", "recording", payload["id"], payload)
        monkeypatch.setattr(mb, "request", lambda path, params=None: {
            "urls": [{"resource": "https://open.spotify.com/track/track-one",
                      "relations": [{"recording": {"id": "recording-one"}}]},
                     {"resource": "https://open.spotify.com/album/album-one",
                      "relations": [{"release": {"id": "release-one"}}]}]})
        links = mb._recording_links([{"user_id": "alice", "spotify_id": "track-one"}])
        assert links[("alice", "track-one")]["recordings"] == {"recording-one"}
        assert links[("alice", "track-one")]["releases"] == {"release-one"}
        selected = mb._select_recording(payload, candidates, links[("alice", "track-one")])
        assert selected[:2] == ("recording-one", "spotify_url")
        linked["recordings"] = {"recording-two", "recording-three"}
        monkeypatch.setattr(mb, "_release_context", lambda _id: [])
        assert mb._select_recording(payload, candidates, linked)[0] is None


def test_multiple_recording_links_and_wrong_relation_do_not_confirm(app, monkeypatch):
    payload, candidates, _ = _recording_fixture()
    with app.app_context():
        mb.enqueue("alice", "recording", payload["id"], payload)
        monkeypatch.setattr(mb, "request", lambda path, params=None: {
            "urls": [{"resource": "https://open.spotify.com/track/track-one",
                      "relations": [{"recording": {"id": "recording-one"}},
                                    {"recording": {"id": "recording-two"}},
                                    {"artist": {"id": "wrong-entity"}}]}]})
        links = mb._recording_links([{"user_id": "alice", "spotify_id": "track-one"}])[("alice", "track-one")]
        assert links["recordings"] == {"recording-one", "recording-two"}
        monkeypatch.setattr(mb, "_release_context", lambda _id: [])
        assert mb._select_recording(payload, candidates, links)[0] is None


def test_isrc_without_results_falls_back_to_name_search(app, monkeypatch):
    calls = []

    def fake_request(path, params=None):
        calls.append(path)
        if path.startswith("isrc/"):
            return {"recordings": []}
        return {"recordings": [{"id": "recording-one", "title": "Song", "length": 180000,
                                "artist-credit": [{"artist": {"name": "Singer"}}]}]}

    monkeypatch.setattr(mb, "request", fake_request)
    payload, _, _ = _recording_fixture()
    with app.app_context():
        assert mb._recording_candidates(payload)[0]["id"] == "recording-one"
    assert calls == ["isrc/USAAA1234567", "recording"]


@pytest.mark.parametrize(("kind", "method"), [("album", "album"), ("ep", "ep")])
def test_recording_release_cascade_requires_membership(app, monkeypatch, kind, method):
    payload, candidates, linked = _recording_fixture()
    release = {"id": "release-one", "title": "Shared Release", "date": "2020-06-01",
               "group_id": "group-one", "group_type": kind, "artist_names": ["Singer"]}
    monkeypatch.setattr(mb, "_release_context", lambda mbid: [release] if mbid == "recording-one" else [])
    with app.app_context():
        selected = mb._select_recording(payload, candidates, linked)
        assert selected[:2] == ("recording-one", method)
        monkeypatch.setattr(mb, "_release_context", lambda _id: [])
        assert mb._select_recording(payload, candidates, linked)[1] != method


def test_release_context_reads_recording_membership_and_group_type(app, monkeypatch):
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        return {"release-count": 1, "releases": [{"id": "release-one", "title": "Shared Release",
                "date": "2020-06-01", "artist-credit": [{"artist": {"name": "Singer"}}],
                "release-group": {"id": "group-one", "primary-type": "EP"}}]}

    monkeypatch.setattr(mb, "request", fake_request)
    with app.app_context():
        context = mb._release_context("recording-one")
    assert calls[0][0] == "release"
    assert calls[0][1]["recording"] == "recording-one"
    assert context[0]["group_type"] == "ep"
    assert context[0]["artist_names"] == ["Singer"]


def test_album_url_is_evidence_only_and_contribution_fallback(app, monkeypatch):
    payload, candidates, linked = _recording_fixture()
    payload["album_name"] = "Unrelated Spotify Label"
    linked["releases"] = {"release-one"}
    release = {"id": "release-one", "title": "Different Title", "date": "2020-06-01",
               "group_id": "group-one", "group_type": "single", "artist_names": ["Other Singer"]}
    monkeypatch.setattr(mb, "_release_context", lambda mbid: [release] if mbid == "recording-one" else [])
    with app.app_context():
        assert mb._select_recording(payload, candidates, linked)[:2] == ("recording-one", "contribution")
        monkeypatch.setattr(mb, "_release_context", lambda _id: [])
        assert mb._select_recording(payload, candidates, linked)[1] != "spotify_url"


def test_contribution_recognizes_featured_artist_on_main_artist_release(app, monkeypatch):
    payload, candidates, linked = _recording_fixture()
    payload["artists"].append({"name": "Guest"})
    candidates[0]["artist_names"] = ["Singer", "Guest"]
    release = {"id": "release-one", "title": "Shared Release", "date": "2020-06-01",
               "group_id": "group-one", "group_type": "single", "artist_names": ["Singer"]}
    monkeypatch.setattr(mb, "_release_context", lambda mbid: [release] if mbid == "recording-one" else [])
    with app.app_context():
        assert mb._select_recording(payload, candidates, linked)[:2] == ("recording-one", "contribution")


def test_recording_score_threshold_margin_and_conflict(app, monkeypatch):
    payload, candidates, linked = _recording_fixture()
    release = {"id": "release-one", "title": "Shared Release", "date": "2020-06-01",
               "group_id": "group-one", "group_type": "single", "artist_names": ["Singer"]}
    monkeypatch.setattr(mb, "_release_context", lambda mbid: [release] if mbid == "recording-one" else [])
    with app.app_context():
        selected = mb._select_recording(payload, candidates, linked)
        assert selected[:2] == ("recording-one", "score")
        assert selected[2]["selected_score"] == 100
        monkeypatch.setattr(mb, "_release_context", lambda _id: [release])
        candidates[1]["isrcs"] = [payload["isrc"]]
        assert mb._select_recording(payload, candidates, linked)[0] is None
        candidates[0]["length"] = 210000
        candidates[1]["length"] = 210000
        assert mb._select_recording(payload, candidates, linked)[0] is None


def test_recheck_backfills_recording_and_preserves_manual_choice(app):
    item = {"spotify_id": "track-one", "album_name": "Shared Release", "album_id": "album-one",
            "album_type": "single", "release_date": "2020-06-01"}
    with app.app_context():
        storage.replace_playlist_items("alice", "playlist", [item])
        mb.enqueue("alice", "recording", "track-one", {"name": "Song", "artists": [{"name": "Singer"}]})
        mb.enqueue("alice", "recording", "manual-track", {"name": "Manual"})
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=1")
        assert mb.set_manual("alice", "recording", "manual-track", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
        mb.enqueue_rechecks()
        match = mb.get_match("alice", "recording", "track-one")
        assert match["spotify"]["album_id"] == "album-one"
        assert mb.status_for("alice")["queued"] == 1
        assert mb.status_for("alice")["recordings"]["ambiguous"] == 1
        assert mb.get_match("alice", "recording", "manual-track")["match_method"] == "manual"


def test_playlist_enqueue_keeps_release_metadata(app):
    item = {"type": "track", "exportable": True, "spotify_id": "track-one", "name": "Song",
            "artists": [{"id": "artist-one", "name": "Singer"}], "album_name": "An EP",
            "album_id": "album-one", "album_type": "single", "release_date": "2020-06"}
    with app.app_context():
        mb.enqueue_playlist("alice", [item])
        stored = mb.get_match("alice", "recording", "track-one")["spotify"]
        assert (stored["album_name"], stored["album_id"], stored["release_date"]) == (
            "An EP", "album-one", "2020-06")


def test_progress_page_uses_completed_jobs_and_is_account_scoped(app):
    client = app.test_client()
    assert client.get("/progress").status_code == 302
    assert client.get("/musicbrainz/progress").status_code == 401
    with app.app_context():
        for spotify_id in ("done", "reviewed", "old-review", "pending"):
            mb.enqueue("alice", "recording", spotify_id, {"name": spotify_id})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET status='confirmed',match_version=2 WHERE spotify_id='done'")
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=2 WHERE spotify_id='reviewed'")
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=1 WHERE spotify_id='old-review'")
            db.execute("DELETE FROM mb_jobs WHERE spotify_id IN ('done','reviewed')")
    login(client, "alice")
    page = client.get("/progress")
    assert page.status_code == 200
    assert b'<progress id="recording-progress"' in page.data
    assert client.get("/musicbrainz/progress").json == {
        "total": 4, "completed": 2, "confirmed": 1, "reviews": 1, "not_found": 0, "queued": 2}
    login(client, "bob")
    assert client.get("/musicbrainz/progress").json["total"] == 0


def test_progress_and_reviews_are_separate_for_artists_and_tracks(app):
    with app.app_context():
        for kind, prefix, count in (("artist", "artist", 22), ("recording", "track", 3)):
            for index in range(count):
                mb.enqueue("alice", kind, f"{prefix}-{index:02}", {"name": f"{prefix}-{index:02}"})
        mb.enqueue("bob", "artist", "private", {"name": "Private"})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=1 "
                       "WHERE user_id='alice' AND kind='artist'")
            db.execute("UPDATE mb_matches SET status='ambiguous',match_version=2 "
                       "WHERE user_id='alice' AND kind='recording'")
            db.execute("DELETE FROM mb_jobs WHERE user_id='alice'")
    client = app.test_client()
    login(client, "alice")
    assert client.get("/musicbrainz/progress?kind=artist").json == {
        "total": 22, "completed": 22, "confirmed": 0, "reviews": 22, "not_found": 0, "queued": 0}
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
    monkeypatch.setattr(mb.time, "time", lambda: next(times))
    monkeypatch.setattr(mb.time, "sleep", lambda seconds: sleeps.append(seconds))
    with app.app_context():
        mb._rate_slot()
        mb._rate_slot()
    assert sleeps == [pytest.approx(1.1)]

    def offline(_req, timeout):
        raise URLError("offline")

    monkeypatch.setattr(mb, "urlopen", offline)
    monkeypatch.setattr(mb, "_rate_slot", lambda: None)
    with app.app_context(), pytest.raises(mb.TemporaryFailure):
        mb.request("artist", {"query": "test"})


def test_confirmed_enrichment_is_kept_without_refresh(app):
    mbid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with app.app_context():
        mb.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
            db.execute("UPDATE mb_matches SET status='confirmed',mbid=?,enriched=?,updated_at=?",
                       (mbid, mb._json({"source": "musicbrainz", "external_id": mbid, "name": "Artist"}),
                        (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()))
        mb.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        assert mb.status_for("alice")["queued"] == 0
        assert mb.get_match("alice", "artist", "artist1")["enriched"]["name"] == "Artist"


def test_unmatched_entity_is_retried_after_seven_days(app):
    with app.app_context():
        mb.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs")
            db.execute("UPDATE mb_matches SET status='not_found',updated_at=?",
                       ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),))
        mb.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        assert mb.status_for("alice")["queued"] == 1


def test_empty_http_result_expires_after_seven_days(app, monkeypatch):
    key = "artist?" + urlencode(sorted({"query": "Artist", "fmt": "json"}.items()))
    with app.app_context():
        with storage.connect() as db:
            db.execute("INSERT INTO mb_http_cache VALUES(?,?,?)",
                       (key, mb._json({"artists": []}),
                        (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()))
        monkeypatch.setattr(mb, "_rate_slot", lambda: None)
        monkeypatch.setattr(mb, "urlopen", lambda request, timeout: BytesIO(b'{"artists":[{"id":"new"}]}'))
        assert mb.request("artist", {"query": "Artist"})["artists"][0]["id"] == "new"


def test_graph_and_pages_expose_provenance(app):
    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": "artist1", "name": "Artist", "genres": ["Rock"]}])
        mb.enqueue("alice", "artist", "artist1", {"id": "artist1", "name": "Artist"})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET status='confirmed',mbid=?,enriched=? WHERE user_id='alice'",
                       ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", mb._json({
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
