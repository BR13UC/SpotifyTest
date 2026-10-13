from flask import Blueprint, jsonify, session

from app.services import artist_sources, geography


map_bp = Blueprint("map", __name__)


@map_bp.get("/data")
def data():
    source, playlist_id = artist_sources.selection()
    return jsonify(geography.map_data(session["spotify_user_id"], source, playlist_id))
