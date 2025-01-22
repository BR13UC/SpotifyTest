import datetime
from flask import Blueprint, jsonify

from app.db_connection import get_collection, set_collection, is_timestamp_stale
from app.helpers import is_valid_token, get_spotify_client

user_bp = Blueprint('user', __name__)

@user_bp.route('/get_profile', methods=['GET'])
def get_profile():
    try:
        # Fetch cached profile
        cached_profile = get_collection('user_profile').find_one()

        # Check if profile is stale or missing
        if not cached_profile or is_timestamp_stale(cached_profile.get("last_updated")):
            if not is_valid_token():
                return jsonify({'error': 'Not authenticated'}), 401

            # Get Spotify client
            spoty_client = get_spotify_client()
            if not spoty_client:
                return jsonify({'error': 'Unable to create Spotify client'}), 500

            # Fetch user profile from Spotify
            user_profile = spoty_client.current_user()

            # Prepare user profile for storage
            user_profile["_id"] = user_profile["id"]  # MongoDB requires a unique _id
            del user_profile["id"]  # Remove the original "id" field
            user_profile["last_updated"] = datetime.datetime.utcnow().isoformat()

            # Store updated profile in the database
            set_collection('user_profile', user_profile)

            return jsonify(user_profile)

        # Return cached profile if available and not stale
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
            spoty_client = get_spotify_client()
            if not spoty_client:
                return jsonify({'error': 'Unable to create Spotify client'}), 500

            artists_list = []
            after = None
            while True:
                response = spoty_client.current_user_followed_artists(limit=50, after=after)
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