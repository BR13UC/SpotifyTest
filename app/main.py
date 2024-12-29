import datetime
import os
from flask import Flask, request, redirect, session, url_for, render_template, jsonify
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler

from db_connection import get_collection, set_collection, is_timestamp_stale
from heplpers import is_valid_token, sp, sp_oauth

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(64)

@app.route('/')
def home():
    if not is_valid_token():
        auth_url = sp_oauth.get_authorize_url()
        return redirect(auth_url)
    return render_template('index.html')

@app.route('/callback')
def callback():
    sp_oauth.get_access_token(request.args['code'])
    return redirect(url_for('home'))

@app.route('/get_profile', methods=['GET'])
def get_profile():
    try:
        cached_profile = get_collection('user_profile').find_one()

        if not cached_profile or is_timestamp_stale(cached_profile.get("last_updated")):
            if not is_valid_token():
                return jsonify({'error': 'Not authenticated'}), 401

            user_profile = sp.current_user()
            user_profile["_id"] = user_profile["id"]
            del user_profile["id"]
            user_profile["last_updated"] = datetime.datetime.utcnow().isoformat()

            set_collection('user_profile', user_profile)
            return jsonify(user_profile)

        return jsonify(cached_profile)
    except Exception as e:
        print(f"Error in get_profile: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/get_followed_artists', methods=['GET'])
def get_followed_artists():
    try:
        cached_artists = get_collection('followed_artists').find_one()

        if not cached_artists or is_timestamp_stale(cached_artists.get("last_updated")):
            print("Fetching followed artists from Spotify API")
            if not is_valid_token():
                return jsonify({'error': 'Not authenticated'}), 401

            artists_list = []
            after = None
            while True:
                response = sp.current_user_followed_artists(limit=50, after=after)
                artists = response['artists']
                artists_list.extend(artists['items'])

                if not artists['next']:
                    break
                after = artists['cursors']['after']

            followed_artists_data = {
                "_id": "followed_artists",
                "total": len(artists_list),
                "artists": artists_list,
                "last_updated": datetime.datetime.utcnow().isoformat()
            }

            set_collection('followed_artists', followed_artists_data)

            return jsonify(followed_artists_data)

        return jsonify(cached_artists)
    except Exception as e:
        print(f"Error in get_followed_artists: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/get_playlists', methods=['GET'])
def get_playlists():
    try:
        cached_playlists = get_collection('playlists').find_one()

        if not cached_playlists or is_timestamp_stale(cached_playlists.get("last_updated")):
            print("Fetching playlists from Spotify API")
            if not is_valid_token():
                return jsonify({'error': 'Not authenticated'}), 401

            playlists_response = sp.current_user_playlists()
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

@app.route('/get_playlist_tracks', methods=['POST'])
def get_playlist_tracks():
    try:
        data = request.json
        playlist_id = data['playlist_id']

        cached_tracks = get_collection('playlist_tracks').find_one({"_id": playlist_id})

        if not cached_tracks or is_timestamp_stale(cached_tracks.get("last_updated")):
            print(f"Fetching tracks for playlist {playlist_id} from Spotify API")
            if not is_valid_token():
                return jsonify({'error': 'Not authenticated'}), 401

            offset = 0
            track_list = []
            while True:
                response = sp.playlist_tracks(playlist_id, offset=offset)
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

@app.route('/get_sort_options', methods=['GET'])
def get_sort_options():
    try:
        track_template = {
            "album": {
                "album_type": "compilation",
                "total_tracks": 9,
                "release_date": "1981-12",
                "name": "string"
            },
            "artists": [
                {
                    "name": "string"
                }
            ],
            "duration_ms": 0,
            "name": "string",
            "popularity": 0,
            "track_number": 0
        }

        dynamic_sort_options = []

        def process_fields(prefix, obj):
            for key, value in obj.items():
                if isinstance(value, dict):
                    process_fields(f"{prefix}.{key}" if prefix else key, value)
                elif isinstance(value, list):
                    if value and isinstance(value[0], dict):
                        process_fields(f"{prefix}.{key}[0]" if prefix else f"{key}[0]", value[0])
                else:
                    display_name_prefix = prefix.replace(".", " ").replace("_", " ").title() if prefix else ""
                    field_name = f"{display_name_prefix} {key.replace('_', ' ').title()}".strip()

                    dynamic_sort_options.append({
                        'id': f'{prefix}.{key}_asc',
                        'name': f'{field_name} (Ascending)',
                        'field': f'{prefix}.{key}' if prefix else f'{key}',
                        'order': 'asc'
                    })
                    dynamic_sort_options.append({
                        'id': f'{prefix}.{key}_desc',
                        'name': f'{field_name} (Descending)',
                        'field': f'{prefix}.{key}' if prefix else key,
                        'order': 'desc'
                    })

        process_fields("", track_template)

        return jsonify(dynamic_sort_options)
    except Exception as e:
        print(f"Error in get_sort_options: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/sort_playlist_tracks', methods=['POST'])
def sort_playlist_tracks():
    try:
        data = request.json
        playlist_id = data['playlist_id']
        sort_option = data.get('sort_option', 'name_asc')

        cached_tracks = get_collection('playlist_tracks').find_one({"_id": playlist_id})
        if not cached_tracks:
            return jsonify({'error': 'Tracks not found in cache for the specified playlist'}), 404

        track_list = cached_tracks['tracks']

        reverse = sort_option.endswith('_desc')
        field_path = sort_option.rsplit('_', 1)[0].lstrip('.')
        field_parts = field_path.split('.')
        print(f"Sorting tracks by field: reverse={reverse}, field_path={field_path}, field_parts={field_parts}")

        def get_nested_field(obj, field_parts):
            try:
                obj = obj.get('track', obj)

                for part in field_parts:
                    if '[' in part and ']' in part:
                        part, index = part.split('[')
                        index = int(index[:-1])
                        obj = obj.get(part, [])[index]
                    else:
                        obj = obj.get(part)
                    if obj is None:
                        return None
                return obj
            except (KeyError, IndexError, TypeError) as e:
                print(f"Error retrieving field '{'.'.join(field_parts)}': {e}")
                return None

        sorted_tracks = sorted(
            track_list,
            key=lambda track: (
                get_nested_field(track, field_parts)
                if isinstance(get_nested_field(track, field_parts), (int, float))
                else str(get_nested_field(track, field_parts) or '')
            ),
            reverse=reverse
        )

        return jsonify({'tracks': sorted_tracks})
    except Exception as e:
        print(f"Error in sort_playlist_tracks: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/create_sorted_playlist', methods=['POST'])
def create_sorted_playlist():
    data = request.json
    track_titles = data.get('track_titles', [])
    playlist_name = data.get('playlist_name', 'Sorted Playlist')

    track_ids = []
    for title in track_titles:
        results = sp.search(q=f'track:{title}', type='track', limit=1)
        items = results['tracks']['items']

        if items:
            track_ids.append(items[0]['id'])
        else:
            print(f"No track found for title: {title}")

    user_id = sp.current_user()['id']
    new_playlist = sp.user_playlist_create(user_id, playlist_name)

    chunk_size = 100
    for i in range(0, len(track_ids), chunk_size):
        chunk = track_ids[i:i + chunk_size]
        sp.playlist_add_items(new_playlist['id'], chunk)

    return 'New playlist created with sorted track titles'

@app.route('/settings')
def settings():
    return render_template('settings.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
