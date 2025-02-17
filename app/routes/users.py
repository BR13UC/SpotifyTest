import datetime
from flask import Blueprint, jsonify

from app.db_connection import get_collection, set_collection, is_timestamp_stale
from app.helpers import is_valid_token, sp

user_bp = Blueprint('user', __name__)

@user_bp.route('/get_profile', methods=['GET'])
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

@user_bp.route('/get_followed_artists', methods=['GET'])
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

@user_bp.route('/get_liked_songs', methods=['GET'])
def get_liked_songs():
    try:
        cached_liked_songs = get_collection('liked_songs').find_one()

        if not cached_liked_songs or is_timestamp_stale(cached_liked_songs.get("last_updated")):
            print("Fetching liked songs from Spotify API")
            if not is_valid_token():
                return jsonify({'error': 'Not authenticated'}), 401

            liked_songs_list = []
            offset = 0
            while True:
                response = sp.current_user_saved_tracks(limit=50, offset=offset)
                liked_songs_list.extend(response['items'])

                if not response['next']:
                    break
                offset += len(response['items'])

            liked_songs_data = {
                "_id": "liked_songs",
                "total": len(liked_songs_list),
                "songs": liked_songs_list,
                "last_updated": datetime.datetime.utcnow().isoformat()
            }

            set_collection('liked_songs', liked_songs_data)

            return jsonify(liked_songs_data)
        return jsonify(cached_liked_songs)
    except Exception as e:
        print(f"Error in get_liked_songs: {e}")
        return jsonify({'error': str(e)}), 500
