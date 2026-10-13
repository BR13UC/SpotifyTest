import hmac
import json
import os
import time
from pathlib import Path
from secrets import token_bytes

from cachelib.file import FileSystemCache
from dotenv import load_dotenv
from flask import Flask, jsonify, request, session
from flask_session import Session
from spotipy.exceptions import SpotifyException
from werkzeug.exceptions import BadRequest, Forbidden, NotFound


def _persistent_secret(data_dir):
    path = data_dir / "secret_key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        for _ in range(20):
            value = path.read_bytes()
            if value:
                return value
            time.sleep(0.05)
        raise RuntimeError("Persistent session key is empty")
    with os.fdopen(fd, "wb") as secret_file:
        secret_file.write(token_bytes(64))
    return path.read_bytes()


def create_app(test_config=None):
    if test_config is None:
        load_dotenv()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    data_dir = Path(os.environ.get("STATIFY_DATA_DIR", app.instance_path))
    app.config.update(
        DATA_DIR=str(data_dir),
        SPOTIPY_CLIENT_ID=os.environ.get("SPOTIPY_CLIENT_ID"),
        SPOTIPY_CLIENT_SECRET=os.environ.get("SPOTIPY_CLIENT_SECRET"),
        SPOTIPY_REDIRECT_URI=os.environ.get("SPOTIPY_REDIRECT_URI", "http://localhost:5000/callback"),
        SESSION_TYPE="cachelib",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=64 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    data_dir = Path(app.config["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    session_dir = data_dir / "sessions"
    session_dir.mkdir(exist_ok=True)
    app.config.setdefault("DATABASE_PATH", str(data_dir / "statify.sqlite3"))
    app.config["SESSION_CACHELIB"] = FileSystemCache(cache_dir=str(session_dir))
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _persistent_secret(data_dir)
    Session(app)

    from app.storage import init_db
    with app.app_context():
        init_db()
        from app.services.ia_store import init_db as init_ia_db
        init_ia_db()

    from app.routes.auth import auth_bp
    from app.routes.graph import graph_bp
    from app.routes.playlists import playlist_bp
    from app.routes.sort import sort_bp
    from app.routes.users import user_bp
    from app.routes.musicbrainz import musicbrainz_bp
    from app.routes.map import map_bp
    from app.routes.timeline import timeline_bp

    from app.routes.pages import router_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(graph_bp, url_prefix="/graphs")
    app.register_blueprint(playlist_bp, url_prefix="/playlists")
    app.register_blueprint(sort_bp, url_prefix="/sorts")
    app.register_blueprint(user_bp, url_prefix="/users")
    app.register_blueprint(musicbrainz_bp, url_prefix="/musicbrainz")
    app.register_blueprint(map_bp, url_prefix="/map")
    app.register_blueprint(timeline_bp, url_prefix="/timeline")
    app.register_blueprint(router_bp)
    from app.routes.ia import ia_bp
    app.register_blueprint(ia_bp, url_prefix="/ia")
    from app.routes.auth import callback
    app.add_url_rule("/callback", endpoint="spotify_callback", view_func=callback)

    @app.before_request
    def protect_api():
        if request.path.startswith("/ia/api/"):
            if not session.get("spotify_user_id"):
                return jsonify(error="Profil local absent. Connectez-vous une première fois.", error_code="local_profile_required"), 401
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                expected = session.get("csrf_token", "")
                supplied = request.headers.get("X-CSRF-Token", "")
                if not expected or not hmac.compare_digest(expected, supplied):
                    return jsonify(error="Jeton de sécurité invalide."), 403
            return None
        if request.path.startswith(("/users/", "/playlists/", "/sorts/", "/graphs/", "/musicbrainz/", "/map/", "/timeline/")):
            from app.spotify_auth import is_valid_token
            if not is_valid_token():
                return jsonify(error="Session Spotify expirée. Reconnectez-vous."), 401
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                expected = session.get("csrf_token", "")
                supplied = request.headers.get("X-CSRF-Token", "")
                if not expected or not hmac.compare_digest(expected, supplied):
                    return jsonify(error="Jeton de sécurité invalide."), 403

    @app.errorhandler(SpotifyException)
    def spotify_error(error):
        status = error.http_status if error.http_status in (401, 403, 429) else 502
        messages = {
            401: "Session Spotify expirée. Reconnectez-vous.",
            403: "Cette playlist n'est pas accessible avec votre compte.",
            429: "Spotify limite temporairement les requêtes. Réessayez plus tard.",
            502: "Spotify est momentanément indisponible.",
        }
        return jsonify(error=messages[status]), status

    @app.errorhandler(BadRequest)
    @app.errorhandler(Forbidden)
    def collection_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(404)
    def not_found(error):
        message = error.description if isinstance(error, NotFound) and error.description != NotFound.description else "Ressource introuvable."
        return jsonify(error=message), 404

    @app.after_request
    def add_error_code(response):
        if response.status_code < 400 or not response.is_json:
            return response
        data = response.get_json(silent=True)
        if not isinstance(data, dict) or "error" not in data or "error_code" in data:
            return response
        codes = {
            "Session Spotify expirée. Reconnectez-vous.": "session_expired",
            "Jeton de sécurité invalide.": "invalid_csrf",
            "Ressource introuvable.": "not_found",
            "Playlist introuvable dans votre bibliothèque.": "playlist_not_found",
            "Identifiant de playlist requis.": "playlist_required",
            "Option de tri inconnue.": "invalid_sort",
            "Artiste suivi introuvable.": "artist_not_found",
            "Artiste introuvable dans la source sélectionnée.": "artist_not_found",
            "Source inconnue.": "invalid_source",
            "Correspondance introuvable.": "match_not_found",
            "Le compte Spotify a changé. Reconnectez-vous.": "account_changed",
            "Chargez d'abord les artistes suivis.": "artists_not_loaded",
            "Accès aux titres likés refusé. Reconnectez-vous et autorisez l'accès à votre bibliothèque Spotify.": "liked_tracks_denied",
            "Format non pris en charge.": "unsupported_format",
            "Décalage invalide.": "invalid_offset",
            "Type inconnu.": "invalid_type",
            "Candidat introuvable.": "candidate_not_found",
            "Catalogues momentanément indisponibles. Réessayez plus tard.": "catalog_unavailable",
            "MBID invalide.": "invalid_mbid",
            "Correspondance ou MBID invalide.": "invalid_match",
            "Playlist, tri et nom requis.": "playlist_details_required",
            "Le nom est trop long.": "name_too_long",
            "Aucun titre Spotify ajoutable.": "no_exportable_tracks",
            "La playlist a été créée, mais l'ajout des titres a été interrompu.": "playlist_partially_created",
            "Cette playlist n'est pas accessible avec votre compte.": "spotify_forbidden",
            "Spotify limite temporairement les requêtes. Réessayez plus tard.": "spotify_rate_limited",
            "Spotify est momentanément indisponible.": "spotify_unavailable",
        }
        data["error_code"] = codes.get(data["error"], "request_failed")
        response.set_data(json.dumps(data, ensure_ascii=False))
        return response

    @app.get("/health")
    def health():
        from app.storage import connect
        with connect() as db:
            db.execute("SELECT 1")
        return jsonify(status="ok")

    return app
