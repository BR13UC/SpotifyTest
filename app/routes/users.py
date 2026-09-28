from flask import Blueprint, jsonify, session

from app import spotify_auth, storage
from app.services import musicbrainz

user_bp = Blueprint("user", __name__)


@user_bp.get("/get_profile")
def get_profile():
    user_id = session["spotify_user_id"]
    cached, updated_at = storage.get_cache(user_id, "profile")
    if cached is None or storage.is_stale(updated_at):
        cached = spotify_auth.get_spotify().current_user()
        if cached.get("id") != user_id:
            return jsonify(error="Le compte Spotify a changé. Reconnectez-vous."), 401
        storage.set_cache(user_id, "profile", cached)
    return jsonify(cached)


@user_bp.get("/get_followed_artists")
def get_followed_artists():
    user_id = session["spotify_user_id"]
    cached, updated_at = storage.get_cache(user_id, "followed_artists")
    if cached is None or storage.is_stale(updated_at):
        artists = []
        after = None
        spotify = spotify_auth.get_spotify()
        while True:
            page = spotify.current_user_followed_artists(limit=50, after=after)["artists"]
            artists.extend(page.get("items", []))
            if not page.get("next"):
                break
            after = page.get("cursors", {}).get("after")
            if not after:
                break
        storage.replace_followed_artists(user_id, artists)
    else:
        artists = storage.get_followed_artists(user_id)
    musicbrainz.enqueue_followed(user_id, artists)
    return jsonify(total=len(artists), artists=artists)
