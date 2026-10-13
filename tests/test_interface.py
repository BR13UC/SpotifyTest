"""UI shell and lightweight overview contracts."""

from flask import session

from app import create_app, spotify_auth, storage
from app.routes import pages


def test_shared_shell_and_settings(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    monkeypatch.setattr(pages, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    client = app.test_client()
    assert client.get("/settings").status_code == 302
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = "alice"
        sess["csrf_token"] = "test"
    for path in ("/home", "/sorts", "/graphs", "/map", "/progress", "/settings"):
        response = client.get(path)
        assert response.status_code == 200
        assert b'id="site-nav"' in response.data
        assert b'id="detail-panel"' in response.data
        assert b'data-en=' in response.data
    assert b'data-theme-option="system"' in client.get("/settings").data
    assert b'data-lang-option="en"' in client.get("/settings").data


def test_home_summary_uses_account_cache_without_fetching_spotify(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(pages, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    client = app.test_client()
    assert client.get("/home/summary").status_code == 401
    with app.app_context():
        storage.set_cache("alice", "playlists", [{"id": "one"}, {"id": "two"}])
        storage.set_cache("bob", "playlists", [{"id": "other"}])
        storage.replace_followed_artists("alice", [{"id": "artist", "name": "Artist"}, {"id": "other", "name": "Other"}])
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = "alice"
    assert client.get("/home/summary").json == {"playlists": 2, "artists": 2, "tracks": None}
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = "bob"
    assert client.get("/home/summary").json == {"playlists": 1, "artists": None, "tracks": None}


def test_api_error_keeps_message_and_adds_stable_code(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    client = app.test_client()
    response = client.get("/sorts/get_sort_options")
    assert response.status_code == 401
    assert response.json == {"error": "Session Spotify expirée. Reconnectez-vous.", "error_code": "session_expired"}


def test_progress_updates_actual_template_in_both_tabs(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import shutil
    import subprocess
    import pytest

    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(pages, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = "alice"
    html = client.get("/progress").get_data(as_text=True)
    subprocess.run(["node", str(Path(__file__).with_name("progress_render.js"))],
                   input=json.dumps({"html": html}), capture_output=True, text=True, check=True, timeout=30)


def test_map_list_groups_sorts_and_preserves_expansion(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import shutil
    import subprocess
    import pytest

    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(pages, "is_valid_token", lambda: bool(session.get("spotify_user_id")))
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["spotify_user_id"] = "alice"
    html = client.get("/map").get_data(as_text=True)
    assert 'aria-labelledby="map-list-title"' in html
    subprocess.run(["node", str(Path(__file__).with_name("map_render.js"))],
                   input=json.dumps({"html": html}), capture_output=True, text=True, check=True, timeout=30)
