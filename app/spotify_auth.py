"""Spotify authentication helpers."""

from flask import current_app, session
from spotipy import Spotify
from spotipy.cache_handler import FlaskSessionCacheHandler
from spotipy.oauth2 import SpotifyOAuth


def get_oauth():
    return SpotifyOAuth(
        client_id=current_app.config["SPOTIPY_CLIENT_ID"],
        client_secret=current_app.config["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=current_app.config["SPOTIPY_REDIRECT_URI"],
        scope="user-follow-read user-library-read playlist-read-private playlist-read-collaborative playlist-modify-private",
        cache_handler=FlaskSessionCacheHandler(session),
    )


def get_spotify():
    return Spotify(auth_manager=get_oauth())


def is_valid_token():
    if not session.get("spotify_user_id"):
        return False
    try:
        oauth = get_oauth()
        return oauth.validate_token(oauth.cache_handler.get_cached_token()) is not None
    except Exception:
        current_app.logger.exception("Spotify token validation failed")
        return False
