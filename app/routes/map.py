from flask import Blueprint, jsonify, session

from app.services import geography


map_bp = Blueprint("map", __name__)


@map_bp.get("/data")
def data():
    return jsonify(geography.map_data(session["spotify_user_id"]))
