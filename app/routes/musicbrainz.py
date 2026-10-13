"""Account-scoped enrichment status and manual matches."""

from flask import Blueprint, jsonify, request, session

from app.services import artist_sources, musicbrainz


musicbrainz_bp = Blueprint("musicbrainz", __name__)


@musicbrainz_bp.get("/status")
def status():
    user_id = session["spotify_user_id"]
    counts = musicbrainz.status_for(user_id)
    if "source" in request.args:
        source, playlist_id = artist_sources.selection()
        counts["artist_revision"] = artist_sources.revision(user_id, source, playlist_id)
    return jsonify(counts=counts, reviews=musicbrainz.reviews_for(user_id, limit=20))


@musicbrainz_bp.get("/progress")
def progress():
    kind = request.args.get("kind", "recording")
    if kind not in ("artist", "recording"):
        return jsonify(error="Type inconnu."), 400
    return jsonify(musicbrainz.progress_for(session["spotify_user_id"], kind=kind))


@musicbrainz_bp.get("/reviews")
def reviews():
    offset = request.args.get("offset", default=0, type=int)
    kind = request.args.get("kind")
    if offset < 0:
        return jsonify(error="Décalage invalide."), 400
    if kind is not None and kind not in ("artist", "recording"):
        return jsonify(error="Type inconnu."), 400
    return jsonify(reviews=musicbrainz.reviews_for(session["spotify_user_id"], limit=20, offset=offset, kind=kind))


@musicbrainz_bp.get("/match/<kind>/<spotify_id>")
def match(kind, spotify_id):
    if kind not in ("artist", "recording"):
        return jsonify(error="Type inconnu."), 400
    result = musicbrainz.get_match(session["spotify_user_id"], kind, spotify_id)
    return jsonify(result) if result else (jsonify(error="Correspondance introuvable."), 404)


@musicbrainz_bp.get("/comparison/artist/<spotify_id>/<mbid>")
def comparison(spotify_id, mbid):
    result = musicbrainz.get_match(session["spotify_user_id"], "artist", spotify_id)
    if not result or mbid not in {candidate["id"] for candidate in result["candidates"]} | {result["mbid"]}:
        return jsonify(error="Candidat introuvable."), 404
    try:
        if (result.get('match_evidence') or {}).get('version') == musicbrainz.ARTIST_MATCH_VERSION:
            from app.services.artist_matching import comparison
            return jsonify(comparison(session['spotify_user_id'], spotify_id, mbid, result))
        return jsonify(musicbrainz.album_comparison(spotify_id, mbid))
    except (musicbrainz.TemporaryFailure, musicbrainz.SpotifyException):
        return jsonify(error="Catalogues momentanément indisponibles. Réessayez plus tard."), 503


@musicbrainz_bp.post("/match/<kind>/<spotify_id>")
def choose_match(kind, spotify_id):
    if kind not in ("artist", "recording"):
        return jsonify(error="Type inconnu."), 400
    data = request.get_json(silent=True) or {}
    mbid = data.get("mbid")
    if mbid is not None and not isinstance(mbid, str):
        return jsonify(error="MBID invalide."), 400
    if not musicbrainz.set_manual(session["spotify_user_id"], kind, spotify_id, mbid):
        return jsonify(error="Correspondance ou MBID invalide."), 400
    return jsonify(musicbrainz.get_match(session["spotify_user_id"], kind, spotify_id))
