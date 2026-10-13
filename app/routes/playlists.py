from flask import Blueprint, jsonify, request, session
from spotipy.exceptions import SpotifyException

from app import spotify_auth
from app.services.playlist_data import get_items_for_user, get_playlists_for_user, sort_items
from app.services import mb_queue
from app import storage

playlist_bp = Blueprint("playlist", __name__)


@playlist_bp.get("/get_playlists")
def get_playlists():
    playlists = get_playlists_for_user(session["spotify_user_id"])
    return jsonify(total=len(playlists), playlists=playlists)


@playlist_bp.post("/get_playlist_tracks")
def get_playlist_tracks():
    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id")
    if not isinstance(playlist_id, str) or not playlist_id:
        return jsonify(error="Identifiant de playlist requis."), 400
    items = get_items_for_user(session["spotify_user_id"], playlist_id)
    if items is None:
        return jsonify(error="Playlist introuvable dans votre bibliothèque."), 404
    spotify_genres = {artist["id"]: artist.get("genres") or [] for artist in storage.get_followed_artists(session["spotify_user_id"])}
    return jsonify(total=len(items), tracks=[{
        **item, "musicbrainz": mb_queue.track_enrichment(session["spotify_user_id"], item, spotify_genres)
    } for item in items])


@playlist_bp.post("/create_sorted_playlist")
def create_sorted_playlist():
    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id")
    sort_option = data.get("sort_option")
    playlist_name = data.get("playlist_name")
    if not all(isinstance(value, str) and value.strip() for value in (playlist_id, sort_option, playlist_name)):
        return jsonify(error="Playlist, tri et nom requis."), 400
    if len(playlist_name) > 100:
        return jsonify(error="Le nom est trop long."), 400
    user_id = session["spotify_user_id"]
    items = get_items_for_user(user_id, playlist_id)
    if items is None:
        return jsonify(error="Playlist introuvable dans votre bibliothèque."), 404
    try:
        ordered = sort_items(items, sort_option)
    except ValueError:
        return jsonify(error="Option de tri inconnue."), 400
    uris = [item["uri"] for item in ordered if item["exportable"]]
    skipped_count = len(ordered) - len(uris)
    if not uris:
        return jsonify(error="Aucun titre Spotify ajoutable."), 400
    spotify = spotify_auth.get_spotify()
    new_playlist = spotify.current_user_playlist_create(playlist_name.strip(), public=False)
    added_count = 0
    for start in range(0, len(uris), 100):
        batch = uris[start:start + 100]
        try:
            spotify.playlist_add_items(new_playlist["id"], batch)
        except SpotifyException:
            return jsonify(
                error="La playlist a été créée, mais l'ajout des titres a été interrompu.",
                id=new_playlist["id"],
                url=(new_playlist.get("external_urls") or {}).get("spotify"),
                created_count=added_count, skipped_count=skipped_count,
            ), 502
        added_count += len(batch)
    return jsonify(
        id=new_playlist["id"],
        url=(new_playlist.get("external_urls") or {}).get("spotify"),
        created_count=len(uris), skipped_count=skipped_count,
    ), 201
