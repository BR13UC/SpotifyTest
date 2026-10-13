from flask import Blueprint, jsonify, request, session
from app.services.playlist_data import SORT_FIELDS, get_items_for_user, sort_items
from app.services import mb_queue
from app import storage

sort_bp = Blueprint("sort", __name__)

@sort_bp.get("/get_sort_options")
def get_sort_options():
    return jsonify([
        {"id": f"{field}_{direction}", "name": f"{label} ({'croissant' if direction == 'asc' else 'décroissant'})"}
        for field, (label, _value_for) in SORT_FIELDS.items()
        for direction in ("asc", "desc")
    ])


@sort_bp.post("/sort_playlist_tracks")
def sort_playlist_tracks():
    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id")
    if not isinstance(playlist_id, str) or not playlist_id:
        return jsonify(error="Identifiant de playlist requis."), 400
    items = get_items_for_user(session["spotify_user_id"], playlist_id)
    if items is None:
        return jsonify(error="Playlist introuvable dans votre bibliothèque."), 404
    try:
        ordered = sort_items(items, data.get("sort_option", "name_asc"))
    except ValueError:
        return jsonify(error="Option de tri inconnue."), 400
    user_id = session["spotify_user_id"]
    spotify_genres = {artist["id"]: artist.get("genres") or [] for artist in storage.get_followed_artists(user_id)}
    return jsonify(tracks=[{**item, "musicbrainz": mb_queue.track_enrichment(user_id, item, spotify_genres)} for item in ordered],
                   skipped_count=sum(not item["exportable"] for item in ordered))
