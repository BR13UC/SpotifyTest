from datetime import datetime, timedelta, timezone
import colorsys
from pathlib import Path
import json
import shutil
import subprocess

import networkx as nx
import pytest
from flask import session
from spotipy.exceptions import SpotifyException

from app import create_app, spotify_auth, storage


class FakeSpotify:
    def __init__(self):
        self.created = []
        self.added = []
        self.fail_items = False
        self.fail_add = False

    def current_user_playlists(self, limit=50, offset=0):
        user = session["spotify_user_id"]
        if offset == 0:
            return {"items": [{"id": f"{user}-playlist", "name": f"Playlist {user}"}], "next": "next"}
        return {"items": [{"id": f"{user}-second", "name": "Autre"}], "next": None}

    def playlist_items(self, playlist_id, limit=50, offset=0):
        if self.fail_items:
            raise SpotifyException(403, -1, "Forbidden")
        if offset:
            return {"items": [
                {"item": {"type": "track", "name": "Alpha", "uri": "spotify:track:alpha", "artists": [{"name": "B"}]}}
            ], "next": None}
        return {"items": [
            {"item": {"type": "track", "name": "Zeta", "uri": "spotify:track:zeta", "artists": [{"name": "A"}]}},
            {"item": None},
            {"item": {"type": "episode", "name": "Podcast", "uri": "spotify:episode:one"}},
            {"item": {"type": "track", "name": "Local", "uri": "spotify:local:one"}, "is_local": True},
        ], "next": "next"}

    def current_user_playlist_create(self, name, public=True):
        self.created.append((name, public))
        return {"id": "new-playlist", "external_urls": {"spotify": "https://open.spotify.com/playlist/new-playlist"}}

    def playlist_add_items(self, playlist_id, uris):
        if self.fail_add:
            raise SpotifyException(429, -1, "Rate limited")
        self.added.append((playlist_id, uris))


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path),
        "SPOTIPY_CLIENT_ID": "test-id",
        "SPOTIPY_CLIENT_SECRET": "test-secret",
        "SPOTIPY_REDIRECT_URI": "http://localhost:5000/callback",
    })
    fake = FakeSpotify()
    monkeypatch.setattr(spotify_auth, "get_spotify", lambda: fake)
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    app.fake_spotify = fake
    return app


def login(client, user="alice"):
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = user
        sess["csrf_token"] = "csrf-test"


def post(client, url, data):
    return client.post(url, json=data, headers={"X-CSRF-Token": "csrf-test"})


def test_authentication_and_csrf(app):
    client = app.test_client()
    assert client.get("/playlists/get_playlists").status_code == 401
    login(client)
    assert client.post("/playlists/get_playlist_tracks", json={"playlist_id": "alice-playlist"}).status_code == 403
    assert client.get("/callback?state=bad&code=abc").status_code == 400


def test_oauth_state_and_callback(app, monkeypatch):
    class FakeOAuth:
        def get_authorize_url(self, state):
            return f"https://accounts.spotify.com/authorize?state={state}"

        def get_access_token(self, code, check_cache=False):
            assert code == "valid-code"

    monkeypatch.setattr(spotify_auth, "get_oauth", lambda: FakeOAuth())
    monkeypatch.setattr(app.fake_spotify, "current_user", lambda: {"id": "alice"}, raising=False)
    client = app.test_client()
    response = client.get("/auth/")
    assert response.status_code == 302
    with client.session_transaction() as sess:
        state = sess["oauth_state"]
    response = client.get(f"/callback?state={state}&code=valid-code")
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["spotify_user_id"] == "alice"
        assert sess["csrf_token"]


def test_oauth_starts_on_callback_host(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path),
                      "SPOTIPY_REDIRECT_URI": "http://127.0.0.1:5000/callback"})
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: False)

    class FakeOAuth:
        def get_authorize_url(self, state):
            return f"https://accounts.spotify.com/authorize?state={state}"

        def get_access_token(self, code, check_cache=False):
            assert code == "valid-code"

    monkeypatch.setattr(spotify_auth, "get_oauth", lambda: FakeOAuth())
    monkeypatch.setattr(spotify_auth, "get_spotify", lambda: type("Spotify", (), {"current_user": lambda self: {"id": "alice"}})())
    client = app.test_client()
    response = client.get("/auth/", base_url="http://localhost:5000")
    assert response.status_code == 302
    assert response.location == "http://127.0.0.1:5000/auth/"
    with client.session_transaction() as sess:
        assert "oauth_state" not in sess
    response = client.get("/auth/", base_url="http://127.0.0.1:5000")
    assert response.location.startswith("https://accounts.spotify.com/authorize?state=")
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(response.location).query)["state"][0]
    assert client.get(f"/callback?state={state}&code=valid-code", base_url="http://127.0.0.1:5000").status_code == 302


def test_pagination_normalization_sort_and_create(app):
    client = app.test_client()
    login(client)
    playlists = client.get("/playlists/get_playlists").json
    assert [playlist["id"] for playlist in playlists["playlists"]] == ["alice-playlist", "alice-second"]
    tracks = post(client, "/playlists/get_playlist_tracks", {"playlist_id": "alice-playlist"}).json["tracks"]
    assert len(tracks) == 5
    assert [item["exportable"] for item in tracks] == [True, False, False, False, True]
    ordered = post(client, "/sorts/sort_playlist_tracks", {
        "playlist_id": "alice-playlist", "sort_option": "name_asc",
    }).json
    assert [item["name"] for item in ordered["tracks"]] == ["Alpha", "Local", "Podcast", "Zeta", "Titre indisponible"]
    assert ordered["skipped_count"] == 3
    descending = post(client, "/sorts/sort_playlist_tracks", {
        "playlist_id": "alice-playlist", "sort_option": "name_desc",
    }).json["tracks"]
    assert descending[-1]["name"] == "Titre indisponible"
    created = post(client, "/playlists/create_sorted_playlist", {
        "playlist_id": "alice-playlist", "sort_option": "name_asc", "playlist_name": "Test privé",
    })
    assert created.status_code == 201
    assert created.json["created_count"] == 2
    assert created.json["skipped_count"] == 3
    assert app.fake_spotify.created == [("Test privé", False)]
    assert app.fake_spotify.added == [("new-playlist", ["spotify:track:alpha", "spotify:track:zeta"])]


def test_account_isolation_and_cache_refresh(app):
    alice = app.test_client()
    bob = app.test_client()
    login(alice, "alice")
    login(bob, "bob")
    assert alice.get("/playlists/get_playlists").json["playlists"][0]["id"] == "alice-playlist"
    assert bob.get("/playlists/get_playlists").json["playlists"][0]["id"] == "bob-playlist"
    assert post(bob, "/playlists/get_playlist_tracks", {"playlist_id": "alice-playlist"}).status_code == 404
    post(alice, "/playlists/get_playlist_tracks", {"playlist_id": "alice-playlist"})
    with app.app_context():
        with storage.connect() as db:
            db.execute(
                "UPDATE cache_entries SET updated_at=? WHERE user_id='alice' AND resource='playlist_items'",
                ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),),
            )
    app.fake_spotify.fail_items = True
    response = post(alice, "/playlists/get_playlist_tracks", {"playlist_id": "alice-playlist"})
    assert response.status_code == 403


def test_partial_playlist_creation_is_reported(app):
    client = app.test_client()
    login(client)
    app.fake_spotify.fail_add = True
    response = post(client, "/playlists/create_sorted_playlist", {
        "playlist_id": "alice-playlist", "sort_option": "name_asc", "playlist_name": "Incomplète",
    })
    assert response.status_code == 502
    assert response.json["id"] == "new-playlist"
    assert response.json["created_count"] == 0
    assert response.json["url"]


def test_graph_homonyms_and_export(app):
    client = app.test_client()
    login(client)
    with app.app_context():
        storage.replace_followed_artists("alice", [
            {"id": "one", "name": "Même nom", "genres": ["rock"]},
            {"id": "two", "name": "Même nom", "genres": ["rock"]},
        ])
    graph = client.get("/graphs/get_artist_genre_graph_data").json
    assert {node["id"] for node in graph["nodes"]} == {"artist:one", "artist:two", "genre:rock"}
    assert len(graph["edges"]) == 2
    assert all("enrichment" not in node for node in graph["nodes"])
    assert client.get("/graphs/artist/one/details").json == {"match": None}
    assert client.get("/graphs/artist/unknown/details").status_code == 404
    response = client.get("/graphs/export_graph/graphml")
    assert response.status_code == 200
    imported = nx.parse_graphml(response.data.decode())
    assert set(imported.nodes) == {"artist:one", "artist:two", "genre:rock"}


def test_playlist_release_year_graph_groups_tracks_and_handles_missing_dates(app, monkeypatch):
    client = app.test_client()
    login(client)
    def playlist_items(_playlist_id, limit=50, offset=0):
        def track(name, release_date):
            return {"item": {"type": "track", "name": name, "artists": [{"name": "Artiste"}],
                             "album": {"name": "Album", "release_date": release_date}}}
        return {"items": [
            track("Ancien", "1999"), track("Même année", "2001-05"),
            track("Même année", "2001-05-17"), track("Sans date", ""),
            track("Date invalide", "2002-13"),
            {"item": {"type": "episode", "name": "Épisode", "album": {"release_date": "2000"}}},
        ], "next": None}
    monkeypatch.setattr(app.fake_spotify, "playlist_items", playlist_items)
    response = post(client, "/graphs/playlist_release_years", {"playlist_id": "alice-playlist"})
    assert response.status_code == 200
    assert response.json["track_count"] == 5
    assert response.json["undated_count"] == 2
    assert [group["year"] for group in response.json["years"]] == [1999, 2001]
    assert [item["name"] for item in response.json["years"][1]["tracks"]] == ["Même année", "Même année"]
    assert [item["position"] for item in response.json["years"][1]["tracks"]] == [1, 2]
    assert post(client, "/graphs/playlist_release_years", {}).status_code == 400
    assert post(client, "/graphs/playlist_release_years", {"playlist_id": "bob-playlist"}).status_code == 404


def test_playlist_release_year_graph_empty_and_auth(app):
    client = app.test_client()
    assert post(client, "/graphs/playlist_release_years", {"playlist_id": "alice-playlist"}).status_code == 401
    login(client)
    assert client.post("/graphs/playlist_release_years", json={"playlist_id": "alice-playlist"}).status_code == 403
    response = post(client, "/graphs/playlist_release_years", {"playlist_id": "alice-playlist"})
    assert response.status_code == 200
    assert response.json["years"] == []
    assert response.json["undated_count"] == 3


def test_liked_artist_counts_pagination_collaborations_cache_and_accounts(app, monkeypatch):
    calls = []
    def saved_tracks(limit=50, offset=0):
        user = session["spotify_user_id"]
        calls.append((user, limit, offset))
        if user == "bob":
            return {"items": [{"track": {"id": "bob-track", "artists": [{"id": "two"}]}}], "next": None}
        if offset == 0:
            return {"items": [
                {"track": {"id": "song-1", "artists": [{"id": "one"}, {"id": "two"}]}},
                {"track": {"id": "song-2", "artists": [{"id": "one"}]}},
                {"track": None},
            ], "next": "next"}
        return {"items": [
            {"track": {"id": "song-1", "artists": [{"id": "one"}]}},
            {"track": {"id": "song-3", "artists": [{"id": "two"}, {"id": "outside"}]}},
        ], "next": None}
    monkeypatch.setattr(app.fake_spotify, "current_user_saved_tracks", saved_tracks, raising=False)
    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": id, "name": id} for id in ("one", "two", "zero")])
        storage.replace_followed_artists("bob", [{"id": "two", "name": "two"}])
    client = app.test_client()
    login(client)
    expected = {"one": 2, "two": 2, "zero": 0}
    assert client.get("/graphs/liked_artist_counts").json["counts"] == expected
    assert calls == [("alice", 50, 0), ("alice", 50, 3)]
    assert client.get("/graphs/liked_artist_counts").json["counts"] == expected
    assert len(calls) == 2
    assert client.get("/graphs/liked_artist_counts?refresh=1").json["counts"] == expected
    assert len(calls) == 4
    login(client, "bob")
    assert client.get("/graphs/liked_artist_counts").json["counts"] == {"two": 1}
    assert calls[-1] == ("bob", 50, 0)


def test_liked_artist_counts_error_does_not_replace_cache(app, monkeypatch):
    from app.services import liked_tracks
    from spotipy.exceptions import SpotifyException

    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": "one", "name": "One"}])
        storage.set_cache("alice", liked_tracks.RESOURCE, {"one": 4})
    def unavailable(**_kwargs):
        raise SpotifyException(429, -1, "Rate limited")
    monkeypatch.setattr(app.fake_spotify, "current_user_saved_tracks", unavailable, raising=False)
    client = app.test_client()
    login(client)
    assert client.get("/graphs/liked_artist_counts?refresh=1").status_code == 429
    assert client.get("/graphs/liked_artist_counts").json["counts"] == {"one": 4}


def test_liked_artist_counts_refreshes_stale_cache(app, monkeypatch):
    from app.services import liked_tracks

    calls = []
    def saved_tracks(**kwargs):
        calls.append(kwargs)
        return {"items": [{"track": {"id": "new", "artists": [{"id": "one"}]}}], "next": None}
    monkeypatch.setattr(app.fake_spotify, "current_user_saved_tracks", saved_tracks, raising=False)
    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": "one", "name": "One"}])
        storage.set_cache("alice", liked_tracks.RESOURCE, {"one": 4})
        with storage.connect() as db:
            db.execute("UPDATE cache_entries SET updated_at=? WHERE user_id=? AND resource=?",
                       ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), "alice", liked_tracks.RESOURCE))
    client = app.test_client()
    login(client)
    assert client.get("/graphs/liked_artist_counts").json["counts"] == {"one": 1}
    assert calls == [{"limit": 50, "offset": 0}]


def test_graph_genre_degree_and_colors(app):
    from app.services import genre_colors

    with app.app_context():
        storage.replace_followed_artists("alice", [
            {"id": "one", "name": "One", "genres": ["indie rock", "synthwave", "unmapped style"]},
            {"id": "two", "name": "Two", "genres": ["indie rock"]},
        ])
    client = app.test_client()
    login(client)
    nodes = {node["id"]: node for node in client.get("/graphs/get_artist_genre_graph_data").json["nodes"]}
    assert nodes["artist:one"]["color"] == "#171717"
    assert nodes["genre:indie rock"]["artist_count"] == 2
    assert nodes["genre:synthwave"]["artist_count"] == 1
    assert nodes["genre:indie rock"]["family"] == "Rock"
    assert nodes["genre:synthwave"]["family"] == "Electronic"
    assert nodes["genre:unmapped style"]["family"] is None
    assert nodes["genre:unmapped style"]["color"] == "#9aa3a9"
    assert genre_colors.color_for("indie rock") == genre_colors.color_for("Indie Rock")
    assert genre_colors.color_for("acid rock")[0] == "Rock"
    assert genre_colors.color_for("acid rock")[1] != genre_colors.color_for("indie rock")[1]
    for node in (nodes["genre:indie rock"], nodes["genre:synthwave"]):
        def hls(color):
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            return colorsys.rgb_to_hls(*channels)
        hue, lightness, saturation = hls(node["color"])
        border_hue, border_lightness, border_saturation = hls(node["border_color"])
        assert min(abs(hue - border_hue), 1 - abs(hue - border_hue)) < 0.01
        assert border_lightness < lightness
        assert saturation > 0.7
        assert abs(saturation - border_saturation) < 0.02


def test_graph_genre_taxonomy_cooccurrence_and_clusters(app):
    from app.services import genre_colors

    assert genre_colors.classify_details("K-pop") == ("East Asian Pop", "K-pop")
    assert genre_colors.classify_details("afrobeats") == ("African", "Afrobeats")
    assert genre_colors.classify_details("indie rock") == ("Rock", "Indie & Alternative")
    with app.app_context():
        storage.replace_followed_artists("alice", [
            {"id": "one", "name": "One", "genres": ["indie rock", "mystery haze"]},
            {"id": "two", "name": "Two", "genres": ["indie rock", "mystery haze"]},
            {"id": "three", "name": "Three", "genres": ["indie rock", "mystery wave"]},
            {"id": "four", "name": "Four", "genres": ["balanced mystery", "rock", "house"]},
        ])
    client = app.test_client()
    login(client)
    nodes = {node["id"]: node for node in client.get("/graphs/get_artist_genre_graph_data").json["nodes"]}
    inferred = nodes["genre:mystery haze"]
    assert inferred["family"] == "Rock"
    assert inferred["subfamily"] == "Indie & Alternative"
    assert inferred["classification_source"] == "cooccurrence"
    assert inferred["color"] != "#9aa3a9"
    assert inferred["cluster"] == "Rock / Indie & Alternative"
    unresolved = nodes["genre:balanced mystery"]
    assert unresolved["family"] is None
    assert unresolved["classification_source"] == "unclassified"
    assert unresolved["cluster"] == "Non classé"


def test_graph_node_sizes():
    if not shutil.which("node"):
        pytest.skip("Node.js absent")
    script = Path(__file__).resolve().parents[1] / "app/static/script.js"
    result = subprocess.run(["node", "-e", """
        const fs = require('fs');
        const vm = require('vm');
        class DataSet { constructor(data) { this.data = data; } }
        class Network {
            constructor(_container, _data, options) { this.options = options; this.calls = []; }
            on() {}
            setOptions(options) { this.calls.push(['setOptions', options]); }
            fit(options) { this.calls.push(['fit', options]); }
            getScale() { return 0.2; }
            getPositions(ids) { return Object.fromEntries(ids.map(id => [id, page.initialPositions.get(id)])); }
            moveTo(options) { this.calls.push(['moveTo', options]); }
        }
        const vis = { DataSet, Network };
        const context = {
            document: { querySelector: () => null, addEventListener: () => {}, getElementById: () => ({}) },
            window: { vis, setTimeout: (_fn, delay) => delay, clearTimeout: () => {} }, vis,
        };
        vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8') + '; globalThis.page = GraphPage;', context);
        const page = context.page;
        page.maxGenreCount = 9;
        page.maxLikedCount = 100;
        page.likedCounts = { one: 100, two: 4 };
        const sizes = [
            page.nodeSize({ group: 'artist', id: 'artist:zero' }),
            page.nodeSize({ group: 'artist', id: 'artist:two' }),
            page.nodeSize({ group: 'artist', id: 'artist:one' }),
            page.nodeSize({ group: 'genre', artist_count: 1 }),
            page.nodeSize({ group: 'genre', artist_count: 9 }),
        ];
        page.likedCounts = null;
        sizes.push(page.nodeSize({ group: 'artist', id: 'artist:one' }));
        const layoutNodes = [
            { id: 'genre:rock', group: 'genre', family: 'Rock', artist_count: 2 },
            { id: 'genre:metal', group: 'genre', family: 'Rock', artist_count: 1 },
            { id: 'genre:house', group: 'genre', family: 'Electronic', artist_count: 1 },
            { id: 'artist:one', group: 'artist' },
            { id: 'artist:two', group: 'artist' },
        ];
        const layoutEdges = [
            { from: 'artist:one', to: 'genre:rock' },
            { from: 'artist:one', to: 'genre:house' },
            { from: 'artist:two', to: 'genre:metal' },
        ];
        page.initialPositions = page.layoutPositions(layoutNodes, layoutEdges);
        const basePositions = [...page.initialPositions];
        page.data = { nodes: layoutNodes };
        page.renderNetwork({
            artists: [{ id: 'artist:one', label: 'One', group: 'artist', color: '#171717', border_color: '#050505' }],
            genres: [{ id: 'genre:rock', label: 'Rock', group: 'genre', color: '#f75b51', border_color: '#a62820', artist_count: 9 }],
            edges: [],
        });
        const options = page.network.options.physics;
        const timeout = page.layoutTimer;
        page.finishLayout();
        const denseNodes = [
            { id: 'genre:rock', group: 'genre', family: 'Rock', artist_count: 12 },
            { id: 'genre:jazz', group: 'genre', family: 'Jazz', artist_count: 8 },
            ...Array.from({ length: 12 }, (_, index) => ({ id: `artist:rock-${index}`, group: 'artist' })),
            ...Array.from({ length: 8 }, (_, index) => ({ id: `artist:jazz-${index}`, group: 'artist' })),
        ];
        const denseEdges = [
            ...Array.from({ length: 12 }, (_, index) => ({ from: `artist:rock-${index}`, to: 'genre:rock' })),
            ...Array.from({ length: 8 }, (_, index) => ({ from: `artist:jazz-${index}`, to: 'genre:jazz' })),
        ];
        page.initialPositions = page.layoutPositions(denseNodes, denseEdges);
        page.data = { nodes: denseNodes };
        page.likedCounts = { 'rock-0': 100 };
        page.maxLikedCount = 100;
        const densePoints = [...page.initialPositions];
        const denseMinimum = densePoints.reduce((minimum, [leftId, left]) => Math.min(minimum, ...densePoints
            .filter(([rightId]) => leftId < rightId)
            .map(([, right]) => Math.hypot(left.x - right.x, left.y - right.y))), Infinity);
        const labels = {
            important: page.shouldShowArtistLabel(denseNodes.find(node => node.id === 'artist:rock-0'), denseNodes, null),
            quiet: page.shouldShowArtistLabel(denseNodes.find(node => node.id === 'artist:rock-1'), denseNodes, null),
            selected: page.shouldShowArtistLabel(denseNodes.find(node => node.id === 'artist:rock-1'), denseNodes, 'artist:rock-1'),
        };
        const weightedNodes = [
            { id: 'genre:hub', group: 'genre', family: 'Rock', cluster: 'Rock / Indie', artist_count: 20 },
            { id: 'genre:neighbor', group: 'genre', family: 'Rock', cluster: 'Rock / Indie', artist_count: 10 },
            { id: 'genre:rare', group: 'genre', family: 'Jazz', cluster: 'Jazz', artist_count: 1 },
        ];
        const weighted = page.layoutPositions(weightedNodes, []);
        const pointDistance = (left, right) => Math.hypot(left.x - right.x, left.y - right.y);
        const weightedLayout = {
            local: pointDistance(weighted.get('genre:hub'), weighted.get('genre:neighbor')),
            remote: pointDistance(weighted.get('genre:hub'), weighted.get('genre:rare')),
            hubRadius: Math.hypot(weighted.get('genre:hub').x, weighted.get('genre:hub').y),
            rareRadius: Math.hypot(weighted.get('genre:rare').x, weighted.get('genre:rare').y),
        };
        process.stdout.write(JSON.stringify({ sizes, nodes: page.nodeSet.data, positions: basePositions,
            repeat: [...page.layoutPositions(layoutNodes, layoutEdges)], options, timeout, calls: page.network.calls,
            denseMinimum, labels, weightedLayout }));
    """, str(script)], check=True, capture_output=True, text=True)
    rendered = json.loads(result.stdout)
    zero, few, many, small_genre, large_genre, unavailable = rendered["sizes"]
    assert zero == 10
    assert 10 < few < many == 28
    assert 18 < small_genre < large_genre == 40
    assert unavailable == 10
    assert [node["color"]["background"] for node in rendered["nodes"]] == ["#171717", "#f75b51"]
    assert [node["color"]["border"] for node in rendered["nodes"]] == ["#050505", "#a62820"]
    assert all(node["borderWidth"] == 1.5 for node in rendered["nodes"])
    assert all("group" not in node for node in rendered["nodes"])
    assert rendered["positions"] == rendered["repeat"]
    positions = dict(rendered["positions"])
    def distance(left, right):
        return ((positions[left]["x"] - positions[right]["x"]) ** 2 +
                (positions[left]["y"] - positions[right]["y"]) ** 2) ** 0.5
    assert distance("genre:rock", "genre:metal") < distance("genre:rock", "genre:house")
    assert distance("artist:one", "genre:rock") < distance("artist:one", "genre:house")
    assert distance("artist:two", "genre:metal") < distance("artist:two", "genre:house")
    assert rendered["options"] is False
    assert "timeout" not in rendered
    assert [call[0] for call in rendered["calls"]] == ["fit", "moveTo"]
    assert rendered["denseMinimum"] >= 60
    assert rendered["labels"] == {"important": True, "quiet": False, "selected": True}
    assert rendered["weightedLayout"]["local"] < rendered["weightedLayout"]["remote"]
    assert rendered["weightedLayout"]["hubRadius"] < rendered["weightedLayout"]["rareRadius"]


def test_graph_batch_matches_and_account_scoped_details(app, monkeypatch):
    from app.services import musicbrainz

    with app.app_context():
        storage.replace_followed_artists("alice", [{"id": "one", "name": "Alice", "genres": ["rock"]}])
        storage.replace_followed_artists("bob", [{"id": "two", "name": "Bob", "genres": ["jazz"]}])
        musicbrainz.enqueue("alice", "artist", "one", {"id": "one", "name": "Alice"})
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET status='confirmed', enriched=?, updated_at=? WHERE user_id='alice'",
                       ('{"genres":[{"name":"indie","source":"musicbrainz","level":"artist"}]}', storage.now()))
    monkeypatch.setattr(musicbrainz, "get_match", lambda *_: pytest.fail("N+1 graph lookup"))
    client = app.test_client()
    login(client)
    response = client.get("/graphs/get_artist_genre_graph_data")
    assert response.status_code == 200
    assert {node["id"] for node in response.json["nodes"]} == {"artist:one", "genre:rock", "genre:indie"}
    assert response.json["revision"]
    assert client.get("/musicbrainz/status").json["counts"]["artist_revision"] == response.json["revision"]
    assert client.get("/graphs/artist/one/details").json["match"]["enriched"]["genres"][0]["name"] == "indie"
    assert client.get("/graphs/artist/two/details").status_code == 404
    login(client, "bob")
    assert client.get("/graphs/artist/one/details").status_code == 404


def test_musicbrainz_reviews_are_paged(app):
    with app.app_context():
        with storage.connect() as db:
            db.executemany(
                "INSERT INTO mb_matches(user_id,kind,spotify_id,spotify_payload,status,updated_at) "
                "VALUES (?, 'artist', ?, '{}', 'ambiguous', ?)",
                [("alice", f"artist-{index:02}", storage.now()) for index in range(25)] +
                [("bob", "artist-secret", storage.now())],
            )
    client = app.test_client()
    login(client)
    first = client.get("/musicbrainz/status").json
    second = client.get("/musicbrainz/reviews?offset=20").json
    assert first["counts"]["ambiguous"] == 25
    assert len(first["reviews"]) == 20
    assert len(second["reviews"]) == 5
    assert second["reviews"][0]["spotify_id"] == "artist-20"
    assert client.get("/musicbrainz/reviews?offset=-1").status_code == 400


def test_session_key_persists(tmp_path):
    first = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    second = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    assert first.secret_key == second.secret_key
