from flask import Blueprint, jsonify, request, session

from app.services import artist_sources, timeline


timeline_bp = Blueprint("timeline", __name__)


@timeline_bp.get("/data")
def data():
    source, playlist_id = artist_sources.selection()
    artists = timeline.artist_data(session["spotify_user_id"], source, playlist_id)
    if artists is None:
        return jsonify(error="Chargez d'abord les artistes suivis."), 404
    return jsonify(artists=artists)


@timeline_bp.get("/artist/<spotify_id>/releases")
def releases(spotify_id):
    offset = request.args.get("offset", default=0, type=int)
    if offset is None or offset < 0:
        return jsonify(error="Décalage invalide."), 400
    source, playlist_id = artist_sources.selection()
    page = timeline.release_page_for(session["spotify_user_id"], spotify_id, offset, source, playlist_id)
    if page is None:
        return jsonify(error="Artiste introuvable dans la source sélectionnée."), 404
    return jsonify(page)
