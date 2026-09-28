from io import BytesIO
import json
import re
from datetime import date

import networkx as nx
from flask import Blueprint, jsonify, request, send_file, session
from spotipy.exceptions import SpotifyException

from app import storage
from app.services import genre_colors, liked_tracks, musicbrainz
from app.services.playlist_data import get_items_for_user

graph_bp = Blueprint("graph", __name__)


def album_release_year(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return None
    try:
        parts = [int(part) for part in value.split("-")]
        date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return None
    return parts[0]


@graph_bp.post("/playlist_release_years")
def playlist_release_years():
    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id")
    if not isinstance(playlist_id, str) or not playlist_id.strip():
        return jsonify(error="Identifiant de playlist requis."), 400
    items = get_items_for_user(session["spotify_user_id"], playlist_id)
    if items is None:
        return jsonify(error="Playlist introuvable dans votre bibliothèque."), 404
    grouped = {}
    undated_count = 0
    track_count = 0
    for item in items:
        if item.get("type") != "track":
            continue
        track_count += 1
        year = album_release_year(item.get("release_date"))
        if year is None:
            undated_count += 1
            continue
        grouped.setdefault(year, []).append({
            "position": item["position"], "name": item["name"],
            "artist_names": item["artist_names"], "album_name": item["album_name"],
        })
    return jsonify(years=[{"year": year, "tracks": grouped[year]} for year in sorted(grouped)],
                   undated_count=undated_count, track_count=track_count)


def graph_records(user_id, include_match=False):
    metadata, _updated_at = storage.get_cache(user_id, "followed_artists")
    if metadata is None:
        return None
    with storage.connect() as db:
        fields = "m.*" if include_match else "m.spotify_id, m.enriched, m.updated_at"
        rows = db.execute(
            f"SELECT f.payload AS artist_payload, {fields} FROM followed_artists AS f "
            "LEFT JOIN mb_matches AS m ON m.user_id=f.user_id AND m.kind='artist' AND m.spotify_id=f.artist_id "
            "WHERE f.user_id=? ORDER BY f.artist_id", (user_id,),
        ).fetchall()
    return [(json.loads(row["artist_payload"]),
             json.loads(row["enriched"]) if row["enriched"] else None,
             musicbrainz._row_dict(row) if include_match and row["spotify_id"] else None,
             row["updated_at"] if row["spotify_id"] else None) for row in rows]


def build_graph(user_id, include_match=False):
    records = graph_records(user_id, include_match)
    if records is None:
        return None, None
    graph = nx.Graph()
    revision = None
    for artist, enriched, match, updated_at in records:
        if updated_at and (revision is None or updated_at > revision):
            revision = updated_at
        artist_id = f"artist:{artist['id']}"
        graph.add_node(artist_id, label=artist.get("name") or "Artiste inconnu", group="artist",
                       enrichment=json.dumps(match, ensure_ascii=False) if match else "")
        spotify_genres = [{"name": genre, "source": "spotify", "level": "artist", "external_id": artist["id"]}
                          for genre in artist.get("genres") or []]
        mb_genres = enriched or {}
        for genre in musicbrainz.merged_genres(spotify_genres, mb_genres.get("genres") or []):
            genre_id = f"genre:{musicbrainz._fold(genre['name'])}"
            graph.add_node(genre_id, label=genre["name"], group="genre")
            graph.add_edge(artist_id, genre_id, origins=json.dumps(genre["origins"], ensure_ascii=False))
    return graph, revision


@graph_bp.get("/get_graph_options")
def get_graph_options():
    return jsonify([
        {"id": "artist_genre", "name": "Artistes suivis et genres"},
        {"id": "release_years", "name": "Titres par année de sortie"},
    ])


@graph_bp.get("/get_artist_genre_graph_data")
def get_artist_genre_graph_data():
    graph, revision = build_graph(session["spotify_user_id"])
    if graph is None:
        return jsonify(error="Chargez d'abord les artistes suivis."), 404
    nodes = []
    for node, data in graph.nodes(data=True):
        if data["group"] == "genre":
            family, color = genre_colors.color_for(data["label"])
            nodes.append({"id": node, "label": data["label"], "group": "genre",
                          "color": color, "border_color": genre_colors.border_for(color),
                          "family": family, "artist_count": graph.degree(node)})
        else:
            nodes.append({"id": node, "label": data["label"], "group": "artist",
                          "color": "#171717", "border_color": "#050505"})
    return jsonify(nodes=nodes, revision=revision, edges=[{"from": source, "to": target,
                                       "origins": json.loads(data["origins"])}
                                      for source, target, data in graph.edges(data=True)])


@graph_bp.get("/liked_artist_counts")
def liked_artist_counts():
    user_id = session["spotify_user_id"]
    try:
        counts = liked_tracks.artist_counts(user_id, refresh=request.args.get("refresh") == "1")
    except SpotifyException as error:
        if error.http_status == 403:
            return jsonify(error="Accès aux titres likés refusé. Reconnectez-vous et autorisez l'accès à votre bibliothèque Spotify."), 403
        raise
    followed_ids = {artist["id"] for artist in storage.get_followed_artists(user_id)}
    return jsonify(counts={artist_id: counts.get(artist_id, 0) for artist_id in followed_ids})


@graph_bp.get("/artist/<spotify_id>/details")
def artist_details(spotify_id):
    user_id = session["spotify_user_id"]
    with storage.connect() as db:
        row = db.execute(
            "SELECT m.* FROM followed_artists AS f "
            "LEFT JOIN mb_matches AS m ON m.user_id=f.user_id AND m.kind='artist' AND m.spotify_id=f.artist_id "
            "WHERE f.user_id=? AND f.artist_id=?", (user_id, spotify_id),
        ).fetchone()
    if row is None:
        return jsonify(error="Artiste suivi introuvable."), 404
    return jsonify(match=musicbrainz._row_dict(row) if row["spotify_id"] else None)


@graph_bp.get("/export_graph/<format>")
def export_graph(format):
    if format != "graphml":
        return jsonify(error="Format non pris en charge."), 400
    graph, _revision = build_graph(session["spotify_user_id"], include_match=True)
    if graph is None:
        return jsonify(error="Chargez d'abord les artistes suivis."), 404
    content = "\n".join(nx.generate_graphml(graph)).encode("utf-8")
    return send_file(
        BytesIO(content), mimetype="application/graphml+xml", as_attachment=True,
        download_name="statify-artistes-genres.graphml",
    )
