from flask import Blueprint, jsonify, session

from app import spotify_auth, storage
from app.services import artist_sources

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
    artists = artist_sources.collection(session["spotify_user_id"], load=True)
    return jsonify(total=len(artists), artists=artists)


@user_bp.get("/artist_collection")
def artist_collection():
    source, playlist_id = artist_sources.selection()
    artists = artist_sources.collection(session["spotify_user_id"], source, playlist_id, load=True)
    return jsonify(source=source, playlist_id=playlist_id, total=len(artists), artists=artists)
