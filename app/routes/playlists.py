import datetime
from flask import Blueprint, request, jsonify

from app.db_connection import get_collection, set_collection, is_timestamp_stale
from app.helpers import is_valid_token, get_spotify_client

playlist_bp = Blueprint('playlist', __name__)

@playlist_bp.route('/get_playlists', methods=['GET'])
def get_playlists():
    try:
        cached_playlists = get_collection('playlists').find_one()

        if not cached_playlists or is_timestamp_stale(cached_playlists.get("last_updated")):
            print("Fetching playlists from Spotify API")
            if not is_valid_token():
                return jsonify({'error': 'Not authenticated'}), 401
            spoty_client = get_spotify_client()
            if not spoty_client:
                return jsonify({'error': 'Unable to create Spotify client'}), 500

            playlists_response = spoty_client.current_user_playlists()
            playlists = playlists_response['items']

            playlists_data = {
                "_id": "playlists",
                "total": len(playlists),
                "playlists": playlists,
                "last_updated": datetime.datetime.utcnow().isoformat()
            }

            set_collection('playlists', playlists_data)

            return jsonify(playlists_data)

        return jsonify(cached_playlists)
    except Exception as e:
        print(f"Error in get_playlists: {e}")
        return jsonify({'error': str(e)}), 500

@playlist_bp.route('/get_playlist_tracks', methods=['POST'])
def get_playlist_tracks():
    try:
        data = request.json
        playlist_id = data['playlist_id']

        cached_tracks = get_collection('playlist_tracks').find_one({"_id": playlist_id})

        if not cached_tracks or is_timestamp_stale(cached_tracks.get("last_updated")):
            print(f"Fetching tracks for playlist {playlist_id} from Spotify API")
            if not is_valid_token():
                return jsonify({'error': 'Not authenticated'}), 401

            spoty_client = get_spotify_client()
            if not spoty_client:
                return jsonify({'error': 'Unable to create Spotify client'}), 500

            offset = 0
            track_list = []
            while True:
                response = spoty_client.playlist_tracks(playlist_id, offset=offset)
                track_list.extend(response['items'])
                if not response['next']:
                    break
                offset += len(response['items'])

            tracks_data = {
                "_id": playlist_id,
                "total": len(track_list),
                "tracks": track_list,
                "last_updated": datetime.datetime.utcnow().isoformat()
            }

            set_collection('playlist_tracks', tracks_data)

            return jsonify(tracks_data)

        return jsonify(cached_tracks)
    except Exception as e:
        print(f"Error in get_playlist_tracks: {e}")
        return jsonify({'error': str(e)}), 500

@playlist_bp.route('/create_sorted_playlist', methods=['POST'])
def create_sorted_playlist():
    data = request.json
    track_titles = data.get('track_titles', [])
    playlist_name = data.get('playlist_name', 'Sorted Playlist')

    track_ids = []
    spoty_client = get_spotify_client()
    if not spoty_client:
        return jsonify({'error': 'Unable to create Spotify client'}), 500

    for title in track_titles:
        results = spoty_client.search(q=f'track:{title}', type='track', limit=1)
        items = results['tracks']['items']

        if items:
            track_ids.append(items[0]['id'])
        else:
            print(f"No track found for title: {title}")

    user_id = spoty_client.current_user()['id']
    new_playlist = spoty_client.user_playlist_create(user_id, playlist_name)

    chunk_size = 100
    for i in range(0, len(track_ids), chunk_size):
        chunk = track_ids[i:i + chunk_size]
        spoty_client.playlist_add_items(new_playlist['id'], chunk)

    return 'New playlist created with sorted track titles'