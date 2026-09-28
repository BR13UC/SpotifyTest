"""Timeline uses Spotify catalogue releases and account-scoped artist dates."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from flask import session

from app import create_app, spotify_auth, storage
from app.routes import pages


class FakeSpotify:
    def __init__(self):
        self.calls = []

    def artist_albums(self, artist_id, album_type, limit, offset):
        self.calls.append((session["spotify_user_id"], artist_id, album_type, limit, offset))
        if offset:
            return {"items": [
                {"id": "one", "name": "Repeated edition", "album_group": "album", "release_date": "2020"},
                {"id": "three", "name": "Collection", "album_group": "compilation", "release_date": "2022-09",
                 "external_urls": {"spotify": "https://open.spotify.com/album/three"}},
            ], "next": None}
        return {"items": [
            {"id": "one", "name": "First", "album_group": "album", "release_date": "2020"},
            {"id": "two", "name": "Second", "album_group": "single", "release_date": "2021-03-04"},
        ], "next": "next"}


def test_timeline_page_and_spotify_discography(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    monkeypatch.setattr(pages, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    spotify = FakeSpotify()
    monkeypatch.setattr(spotify_auth, "get_spotify", lambda: spotify)
    client = app.test_client()
    assert client.get("/timeline").status_code == 302
    assert client.get("/timeline/data").status_code == 401
    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": "artist-one", "name": "Singer"}])
        storage.replace_followed_artists("bob", [{"id": "artist-two", "name": "Group"}])
        with storage.connect() as db:
            db.execute("INSERT INTO mb_matches(user_id,kind,spotify_id,spotify_payload,status,enriched,updated_at) "
                       "VALUES(?,?,?,?,?,?,?)", ("alice", "artist", "artist-one", '{}', "confirmed",
                       json.dumps({"type": "Person", "dates": {"begin": "1980-06", "end": None, "ended": False}}),
                       storage.now()))
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = "alice"
    assert client.get("/timeline").status_code == 200
    data = client.get("/timeline/data").json["artists"]
    assert data == [{"id": "artist-one", "name": "Singer", "type": "Person",
                     "dates": {"begin": "1980-06", "end": None, "ended": False}, "match_status": "confirmed"}]
    url = "/timeline/artist/artist-one/releases"
    first_page = client.get(url).json
    second_page = client.get(url + "?offset=2").json
    assert first_page["next_offset"] == 2
    assert second_page["next_offset"] is None
    first = first_page["releases"] + second_page["releases"]
    assert [(item["id"], item["category"], item["date"]) for item in first] == [
        ("one", "album", "2020"), ("two", "single", "2021-03-04"),
        ("one", "album", "2020"), ("three", "compilation", "2022-09")]
    assert first[-1]["url"] == "https://open.spotify.com/album/three"
    assert client.get(url).json == first_page
    assert client.get(url + "?offset=2").json == second_page
    assert spotify.calls == [("alice", "artist-one", "album,single,compilation", 10, 0),
                            ("alice", "artist-one", "album,single,compilation", 10, 2)]
    assert client.get("/timeline/artist/artist-two/releases").status_code == 404
    assert client.get(url + "?offset=-1").status_code == 400
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = "bob"
    assert [item["id"] for item in client.get("/timeline/data").json["artists"]] == ["artist-two"]
    assert client.get(url).status_code == 404
    assert client.get("/timeline/artist/artist-two/releases").status_code == 200
    assert spotify.calls[-1][:2] == ("bob", "artist-two")


def test_older_musicbrainz_artist_is_queued_for_life_status_refresh(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": "artist-one", "name": "Singer"}])
        with storage.connect() as db:
            db.execute("INSERT INTO mb_matches(user_id,kind,spotify_id,spotify_payload,status,mbid,enriched,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?)", ("alice", "artist", "artist-one", '{}', "confirmed",
                       "00000000-0000-0000-0000-000000000001",
                       json.dumps({"dates": {"begin": "1980", "end": None}}), storage.now()))
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = "alice"
    assert client.get("/timeline/data").status_code == 200
    with app.app_context():
        with storage.connect() as db:
            jobs = db.execute("SELECT spotify_id FROM mb_jobs WHERE user_id='alice'").fetchall()
    assert [row["spotify_id"] for row in jobs] == ["artist-one"]


def test_timeline_date_sorting_and_life_states():
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    core = Path(__file__).resolve().parents[1] / "app/static/timeline_core.js"
    script = """
      const assert = require('node:assert/strict');
      const core = require(process.argv[1]);
      assert.equal(core.datePosition('1980'), 1980);
      assert.ok(core.datePosition('1980-06') > 1980);
      assert.ok(core.datePosition('1980-06-15') > core.datePosition('1980-06'));
      assert.equal(core.datePosition('1980-02-30'), null);
      const artists = [
        {id: 'old', name: 'Old', dates: {begin: '1950'}},
        {id: 'unknown', name: 'Unknown', dates: null},
        {id: 'young', name: 'Young', dates: {begin: '2000-06'}},
        {id: 'future', name: 'Future', dates: {begin: '2030'}},
      ];
      const sorted = core.sortArtists(artists);
      assert.deepEqual(sorted.dated.map(item => item.id), ['future', 'young', 'old']);
      assert.deepEqual(sorted.undated.map(item => item.id), ['unknown']);
      assert.equal(core.lifeState({end: '2001', ended: true}), 'ended');
      assert.equal(core.lifeState({end: null, ended: false}), 'active');
      assert.equal(core.lifeState({end: null}), 'unknown');
      assert.equal(core.lifeState({end: null, ended: true}), 'unknown');
      assert.deepEqual(core.mergeReleases([[{id: 'a'}, {id: 'b'}], [{id: 'a'}, {id: 'c'}]]).map(item => item.id), ['a', 'b', 'c']);
    """
    subprocess.run(["node", "-e", script, str(core)], check=True, capture_output=True, text=True)


def test_timeline_renders_undated_artists_in_chart():
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    script = Path(__file__).with_name("timeline_render.js")
    subprocess.run(["node", str(script)], check=True, capture_output=True, text=True)
