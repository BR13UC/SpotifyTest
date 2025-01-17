from flask import Blueprint, jsonify

from app.db_connection import get_collection
from app.helpers import export_artist_genre_graphml, get_genre_color_map, get_artist_color

graph_bp = Blueprint('graph', __name__)

@graph_bp.route('/get_graph_options', methods=['GET'])
def get_graph_options():
    try:
        graph_options = [
            {'id': '0', 'name': 'Liked Artists'},
            {'id': '1', 'name': 'Playlist by Genre'},
            {'id': '2', 'name': 'Playlist by Artist'},
            {'id': '3', 'name': 'Artists Comparator'}
        ]
        return jsonify(graph_options)
    except Exception as e:
        print(f"Error in get_graph_options: {e}")
        return jsonify({'error': str(e)}), 500

@graph_bp.route('/export_graph/<format>', methods=['GET'])
def export_graph(format):
    try:
        if format == "graphml":
            export_artist_genre_graphml()
            return jsonify({'file_url': '/static/artist_genre.graphml'})
        else:
            return jsonify({'error': 'Unsupported format'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@graph_bp.route('/get_artist_genre_graph_data', methods=['GET'])
def get_artist_genre_graph_data():
    try:
        collection = get_collection("followed_artists")
        followed_artists_data = collection.find_one()

        if not followed_artists_data:
            return jsonify({'error': 'No data found'}), 404

        nodes = {}
        edges = []
        artists = followed_artists_data.get("artists", [])

        for artist in artists:
            artist_name = artist.get("name", "Unknown Artist")
            artist_genres = artist.get("genres", [])
            artist_color = "#cccccc"
            artist_size = 10
            print(f"Processing artist: {artist_name}, Genres: {artist_genres}")

            nodes[artist_name] = {
                'id': artist_name,
                'label': artist_name,
                'group': 'artist',
                'color': artist_color,
                'size': artist_size
            }

            for genre in artist_genres:
                if genre not in nodes:
                    nodes[genre] = {
                        'id': genre,
                        'label': genre,
                        'group': 'genre',
                        'color': "#ffcc00"
                    }
                edges.append({'from': artist_name, 'to': genre})

        return jsonify({'nodes': list(nodes.values()), 'edges': edges})

    except Exception as e:
        print(f"Error in /get_artist_genre_graph_data: {e}")
        return jsonify({'error': str(e)}), 500
