"""Collection membership, visualizations and Spotify cache contracts."""

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import shutil
import subprocess

import networkx as nx
import pytest
from flask import session
from spotipy.exceptions import SpotifyException

from app import create_app, spotify_auth, storage
from app.routes import pages
from app.services import liked_tracks


def track(track_id, artists, year="2020"):
    return {"track": {"id": track_id, "type": "track", "name": track_id,
                      "uri": f"spotify:track:{track_id}",
                      "artists": [{"id": artist, "name": artist.title()} for artist in artists],
                      "album": {"name": "Album", "release_date": year}}}


class CollectionSpotify:
    def __init__(self):
        self.calls = []
        self.fail_offset = None
        self.empty = False

    def current_user_saved_tracks(self, limit=50, offset=0):
        user = session["spotify_user_id"]
        self.calls.append((user, "liked", offset))
        if offset == self.fail_offset:
            raise SpotifyException(429, -1, "limited")
        if self.empty:
            return {"items": [], "next": None}
        if user == "bob":
            return {"items": [track("bob-only", ["bob-artist"])], "next": None}
        if offset:
            return {"items": [track("t1", ["one", "two"]), track("t4", ["two"], "2022")], "next": None}
        return {"items": [track("t1", ["one", "two", "one"]), track("t2", ["two"], "2021"),
                          track("t3", ["one"], ""), {"track": None},
                          {"track": {"id": "local", "is_local": True, "artists": [{"id": "local-artist"}]}}],
                "next": "next"}

    def current_user_followed_artists(self, limit=50, after=None):
        self.calls.append((session["spotify_user_id"], "followed", after))
        if after:
            return {"artists": {"items": [{"id": "zero", "name": "Zero", "genres": ["rock"]}], "next": None}}
        return {"artists": {"items": [{"id": "one", "name": "One", "genres": ["rock"]}],
                            "next": "next", "cursors": {"after": "one"}}}

    def current_user_playlists(self, limit=50, offset=0):
        user = session["spotify_user_id"]
        return {"items": [{"id": f"{user}-playlist", "name": "Test playlist"}], "next": None}

    def playlist_items(self, playlist_id, limit=50, offset=0):
        self.calls.append((session["spotify_user_id"], "playlist", offset))
        if offset:
            return {"items": [track("p1", ["two", "three"], "2018"),
                              {"track": {"type": "episode", "id": "episode", "artists": [{"id": "podcast"}]}}],
                    "next": None}
        return {"items": [track("p1", ["two", "three"], "2018")], "next": "next"}

    def artist_albums(self, artist_id, album_type, limit, offset):
        self.calls.append((session["spotify_user_id"], "releases", artist_id))
        return {"items": [{"id": f"{artist_id}-unliked", "name": "Full discography release",
                           "album_group": "album", "release_date": "1999"}], "next": None}

    def current_user(self):
        return {"id": session["spotify_user_id"], "display_name": "Demo"}


def seed(app):
    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": "one", "name": "One", "genres": ["rock"]},
                                                   {"id": "zero", "name": "Zero", "genres": ["rock"]}])
        for artist_id, country in (("one", "FR"), ("zero", "FR"), ("two", "GB")):
            enriched = {"type": "Person", "country_code": country,
                        "dates": {"begin": "1980", "end": None, "ended": False},
                        "genres": [{"name": "rock", "source": "musicbrainz", "level": "artist"}]}
            with storage.connect() as db:
                db.execute("INSERT INTO mb_matches(user_id,kind,spotify_id,spotify_payload,status,enriched,manual,updated_at) "
                           "VALUES(?,'artist',?,?,'confirmed',?,1,?)",
                           ("alice", artist_id, json.dumps({"id": artist_id, "name": artist_id.title()}),
                            json.dumps(enriched), storage.now()))


@pytest.fixture
def collections(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    spotify = CollectionSpotify()
    monkeypatch.setattr(spotify_auth, "get_spotify", lambda: spotify)
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    monkeypatch.setattr(pages, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    seed(app)
    client = app.test_client()
    login(client)
    return app, client, spotify


def login(client, user="alice"):
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = user
        sess["csrf_token"] = "test"


@pytest.mark.parametrize("query,expected,counts", [
    ("source=followed", {"one", "zero"}, {"one": 2, "zero": 0}),
    ("source=liked", {"one", "two"}, {"one": 2, "two": 3}),
    ("source=playlist&playlist_id=alice-playlist", {"two", "three"}, {"two": 3, "three": 0}),
])
def test_all_visualizations_and_export_share_membership(collections, query, expected, counts):
    app, client, spotify = collections
    result = client.get(f"/users/artist_collection?{query}")
    assert result.status_code == 200
    assert {artist["id"] for artist in result.json["artists"]} == expected
    graph = client.get(f"/graphs/get_artist_genre_graph_data?{query}").json
    assert {node["id"][7:] for node in graph["nodes"] if node["group"] == "artist"} == expected
    rock = next(node for node in graph["nodes"] if node["id"] == "genre:rock")
    assert rock["artist_count"] == len(expected - {"three"})
    assert client.get(f"/graphs/liked_artist_counts?{query}").json["counts"] == counts
    timeline = client.get(f"/timeline/data?{query}").json
    assert {artist["id"] for artist in timeline["artists"]} == expected
    origins = client.get(f"/map/data?{query}").json
    assert origins["counts"]["total"] == len(expected)
    assert origins["counts"]["unknown"] == int("three" in expected)
    assert {artist["id"] for marker in origins["markers"] for artist in marker["artists"]} == expected - {"three"}
    exported = nx.read_graphml(BytesIO(client.get(f"/graphs/export_graph/graphml?{query}").data))
    assert {node[7:] for node in exported if node.startswith("artist:")} == expected
    for artist in expected:
        assert client.get(f"/graphs/artist/{artist}/details?{query}").status_code == 200
        releases = client.get(f"/timeline/artist/{artist}/releases?{query}").json["releases"]
        assert releases[0]["id"] == f"{artist}-unliked"
    absent = next(iter({"one", "zero", "two", "three"} - expected))
    assert client.get(f"/graphs/artist/{absent}/details?{query}").status_code == 404
    assert client.get(f"/timeline/artist/{absent}/releases?{query}").status_code == 404
    # The library is paginated once, even when used by several views.
    assert [call for call in spotify.calls if call[1] == "liked"] == [("alice", "liked", 0), ("alice", "liked", 5)]
    with app.app_context():
        assert {artist["id"] for artist in storage.get_followed_artists("alice")} == {"one", "zero"}
        if "three" in expected:
            with storage.connect() as db:
                assert db.execute("SELECT 1 FROM mb_jobs WHERE user_id='alice' AND spotify_id='three'").fetchone()


@pytest.mark.parametrize("query,total,undated,years", [
    ("source=followed", 2, 1, [(2020, 1)]),
    ("source=liked", 4, 1, [(2020, 1), (2021, 1), (2022, 1)]),
    ("source=playlist&playlist_id=alice-playlist", 2, 0, [(2018, 2)]),
])
def test_release_years_filter_and_deduplication(collections, query, total, undated, years):
    _, client, _ = collections
    data = client.get(f"/graphs/release_years?{query}").json
    assert data["track_count"] == total
    assert data["undated_count"] == undated
    assert [(group["year"], len(group["tracks"])) for group in data["years"]] == years


def test_collection_cache_is_atomic_and_refreshable(collections):
    app, client, spotify = collections
    client.get("/users/artist_collection?source=liked")
    with app.app_context():
        before = {resource: storage.get_cache("alice", resource)
                  for resource in (liked_tracks.RESOURCE, liked_tracks.COLLECTION_RESOURCE)}
    spotify.fail_offset = 5
    response = client.get("/graphs/liked_artist_counts?source=liked&refresh=1")
    assert response.status_code == 429
    with app.app_context():
        assert {resource: storage.get_cache("alice", resource) for resource in before} == before
        stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with storage.connect() as db:
            db.execute("UPDATE cache_entries SET updated_at=? WHERE user_id='alice' AND resource IN (?,?)",
                       (stale, liked_tracks.RESOURCE, liked_tracks.COLLECTION_RESOURCE))
    spotify.fail_offset = None
    spotify.empty = True
    assert client.get("/users/artist_collection?source=liked").json["total"] == 0
    assert client.get("/graphs/get_artist_genre_graph_data?source=liked").json["nodes"] == []
    assert client.get("/graphs/liked_artist_counts?source=liked").json["counts"] == {}
    assert client.get("/map/data?source=liked").json["counts"]["total"] == 0
    assert client.get("/timeline/data?source=liked").json["artists"] == []


def test_validation_denied_library_and_account_isolation(collections, monkeypatch):
    _, client, spotify = collections
    for path in ("/users/artist_collection", "/graphs/get_artist_genre_graph_data", "/graphs/release_years",
                 "/graphs/liked_artist_counts", "/graphs/export_graph/graphml", "/map/data", "/timeline/data",
                 "/timeline/artist/one/releases", "/graphs/artist/one/details", "/musicbrainz/status"):
        for query in ("source=invalid", "source=playlist"):
            response = client.get(f"{path}?{query}")
            assert response.status_code == 400, (path, response.data)
            assert response.is_json
    assert client.get("/users/artist_collection?source=playlist&playlist_id=bob-playlist").status_code == 404
    client.get("/users/artist_collection?source=liked")
    client.get("/timeline/artist/two/releases?source=liked")
    # A cached catalogue cannot bypass membership in a different source/account.
    assert client.get("/timeline/artist/two/releases?source=followed").status_code == 404
    login(client, "bob")
    assert {artist["id"] for artist in client.get("/users/artist_collection?source=liked").json["artists"]} == {"bob-artist"}
    assert client.get("/timeline/artist/two/releases?source=liked").status_code == 404
    assert client.get("/graphs/artist/two/details?source=liked").status_code == 404
    def denied(**_):
        raise SpotifyException(403, -1, "denied")
    monkeypatch.setattr(spotify, "current_user_saved_tracks", denied)
    response = client.get("/graphs/liked_artist_counts?source=liked&refresh=1")
    assert response.status_code == 403
    assert response.json["error_code"] == "liked_tracks_denied"


def test_revision_only_tracks_selected_artists(collections):
    app, client, _ = collections
    client.get("/users/artist_collection?source=liked")
    with app.app_context(), storage.connect() as db:
        db.execute("UPDATE mb_matches SET updated_at='2099-01-01' WHERE spotify_id='zero'")
    graph_revision = client.get("/graphs/get_artist_genre_graph_data?source=liked").json["revision"]
    assert client.get("/musicbrainz/status?source=liked").json["counts"]["artist_revision"] == graph_revision
    assert graph_revision != "2099-01-01"


def test_source_selector_cancellation_and_context():
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    subprocess.run(["node", str(Path(__file__).with_name("artist_source.js"))],
                   check=True, capture_output=True, text=True, timeout=30)


def test_legacy_count_cache_upgrades_and_cached_spotify_genres_are_reused(collections):
    app, client, _ = collections
    with app.app_context():
        storage.set_cache("alice", liked_tracks.RESOURCE, {"one": 99})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET spotify_payload=? WHERE spotify_id='two'",
                       (json.dumps({"id": "two", "name": "Two", "genres": ["jazz"]}),))
    assert client.get("/graphs/liked_artist_counts?source=liked").json["counts"] == {"one": 2, "two": 3}
    graph = client.get("/graphs/get_artist_genre_graph_data?source=liked").json
    assert any(node["id"] == "genre:jazz" for node in graph["nodes"])
    jazz_edge = next(edge for edge in graph["edges"] if "genre:jazz" in (edge["from"], edge["to"]))
    assert jazz_edge["origins"][0]["source"] == "spotify"
