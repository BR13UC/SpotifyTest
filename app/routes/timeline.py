from flask import Blueprint, jsonify, request, session

from app.services import timeline


timeline_bp = Blueprint("timeline", __name__)


@timeline_bp.get("/data")
def data():
    artists = timeline.artist_data(session["spotify_user_id"])
    if artists is None:
        return jsonify(error="Chargez d'abord les artistes suivis."), 404
    return jsonify(artists=artists)


@timeline_bp.get("/artist/<spotify_id>/releases")
def releases(spotify_id):
    offset = request.args.get("offset", default=0, type=int)
    if offset is None or offset < 0:
        return jsonify(error="Décalage invalide."), 400
    page = timeline.release_page_for(session["spotify_user_id"], spotify_id, offset)
    if page is None:
        return jsonify(error="Artiste introuvable parmi les artistes suivis."), 404
    return jsonify(page)
