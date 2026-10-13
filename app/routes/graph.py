from io import BytesIO
import json
import re
from datetime import date

import networkx as nx
from flask import Blueprint, jsonify, request, send_file, session
from spotipy.exceptions import SpotifyException

from app import storage
from app.services import artist_sources, genre_colors, liked_tracks, mb_client, mb_queue
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
    return release_year_response(items)


@graph_bp.get("/release_years")
def release_years():
    source, playlist_id = artist_sources.selection()
    return release_year_response(artist_sources.tracks(session["spotify_user_id"], source, playlist_id))


def release_year_response(items):
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


def graph_records(user_id, include_match=False, source="followed", playlist_id=None):
    records = artist_sources.records(user_id, source, playlist_id)
    if records is None:
        return None
    return [(artist,
             json.loads(row["enriched"]) if row and row["enriched"] else None,
             mb_queue._row_dict(row) if include_match and row else None,
             row["updated_at"] if row else None) for artist, row in records]


def build_graph(user_id, include_match=False, source="followed", playlist_id=None):
    records = graph_records(user_id, include_match, source, playlist_id)
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
        for genre in mb_queue.merged_genres(spotify_genres, mb_genres.get("genres") or []):
            genre_id = f"genre:{mb_client._fold(genre['name'])}"
            graph.add_node(genre_id, label=genre["name"], group="genre")
            graph.add_edge(artist_id, genre_id, origins=json.dumps(genre["origins"], ensure_ascii=False))
    return graph, revision


def resolve_genre_groups(graph):
    """Classify genres locally, then use shared artists only for unknowns."""
    groups = {}
    for node, data in graph.nodes(data=True):
        if data["group"] != "genre":
            continue
        family, subfamily = genre_colors.classify_details(data["label"])
        groups[node] = {"family": family, "subfamily": subfamily,
                        "source": "taxonomy" if family else "unclassified"}
    for node, group in groups.items():
        if group["family"] is not None:
            continue
        votes = {}
        for artist in graph.neighbors(node):
            for other in graph.neighbors(artist):
                if other == node or other not in groups:
                    continue
                other_group = groups[other]
                if other_group["source"] != "taxonomy":
                    continue
                key = other_group["family"], other_group["subfamily"]
                votes[key] = votes.get(key, 0) + 1
        if votes:
            highest = max(votes.values())
            winners = [key for key, count in votes.items() if count == highest]
            if len(winners) == 1:
                group["family"], group["subfamily"] = winners[0]
                group["source"] = "cooccurrence"

    clusters = {}
    for node, group in groups.items():
        if group["family"] is None:
            cluster = "Non classé"
        else:
            key = group["family"], group["subfamily"]
            clusters.setdefault(key, {"genres": 0, "artists": 0})
            clusters[key]["genres"] += 1
            clusters[key]["artists"] += graph.degree(node)
            cluster = None
        group["cluster"] = cluster
    for group in groups.values():
        if group["family"] is None:
            continue
        key = group["family"], group["subfamily"]
        stats = clusters[key]
        group["cluster"] = f"{key[0]} / {key[1]}" if stats["genres"] >= 3 or stats["artists"] >= 6 else key[0]
    return groups


@graph_bp.get("/get_graph_options")
def get_graph_options():
    return jsonify([
        {"id": "artist_genre", "name": "Artistes et genres"},
        {"id": "release_years", "name": "Titres par année de sortie"},
    ])


@graph_bp.get("/get_artist_genre_graph_data")
def get_artist_genre_graph_data():
    source, playlist_id = artist_sources.selection()
    graph, revision = build_graph(session["spotify_user_id"], source=source, playlist_id=playlist_id)
    if graph is None:
        return jsonify(error="Chargez d'abord les artistes suivis."), 404
    groups = resolve_genre_groups(graph)
    nodes = []
    for node, data in graph.nodes(data=True):
        if data["group"] == "genre":
            group = groups[node]
            family = group["family"]
            color = genre_colors.color_for_family(data["label"], family)
            nodes.append({"id": node, "label": data["label"], "group": "genre",
                          "color": color, "border_color": genre_colors.border_for(color),
                          "family": family, "subfamily": group["subfamily"],
                          "cluster": group["cluster"], "classification_source": group["source"],
                          "artist_count": graph.degree(node)})
        else:
            nodes.append({"id": node, "label": data["label"], "group": "artist",
                          "color": "#171717", "border_color": "#050505"})
    return jsonify(nodes=nodes, revision=revision, edges=[{"from": source, "to": target,
                                       "origins": json.loads(data["origins"])}
                                      for source, target, data in graph.edges(data=True)])


@graph_bp.get("/liked_artist_counts")
def liked_artist_counts():
    user_id = session["spotify_user_id"]
    source, playlist_id = artist_sources.selection()
    try:
        refresh = request.args.get("refresh") == "1"
        if source == "liked" and not refresh:
            liked_tracks.collection(user_id)
        counts = liked_tracks.artist_counts(user_id, refresh=refresh)
    except SpotifyException as error:
        if error.http_status == 403:
            return jsonify(error="Accès aux titres likés refusé. Reconnectez-vous et autorisez l'accès à votre bibliothèque Spotify."), 403
        raise
    artists = artist_sources.collection(user_id, source, playlist_id) or []
    return jsonify(counts={artist["id"]: counts.get(artist["id"], 0) for artist in artists})


@graph_bp.get("/artist/<spotify_id>/details")
def artist_details(spotify_id):
    user_id = session["spotify_user_id"]
    source, playlist_id = artist_sources.selection()
    artists = artist_sources.collection(user_id, source, playlist_id) or []
    if spotify_id not in {artist["id"] for artist in artists}:
        return jsonify(error="Artiste introuvable dans la source sélectionnée."), 404
    with storage.connect() as db:
        row = db.execute("SELECT * FROM mb_matches WHERE user_id=? AND kind='artist' AND spotify_id=?",
                         (user_id, spotify_id)).fetchone()
    return jsonify(match=mb_queue._row_dict(row) if row else None)


@graph_bp.get("/export_graph/<format>")
def export_graph(format):
    if format != "graphml":
        return jsonify(error="Format non pris en charge."), 400
    source, playlist_id = artist_sources.selection()
    graph, _revision = build_graph(session["spotify_user_id"], include_match=True, source=source, playlist_id=playlist_id)
    if graph is None:
        return jsonify(error="Chargez d'abord les artistes suivis."), 404
    content = "\n".join(nx.generate_graphml(graph)).encode("utf-8")
    return send_file(
        BytesIO(content), mimetype="application/graphml+xml", as_attachment=True,
        download_name="statify-artistes-genres.graphml",
    )
