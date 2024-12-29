import os
from flask import session
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler
from dotenv import load_dotenv

load_dotenv()

sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope='playlist-read-private playlist-modify-public playlist-modify-private',
    cache_handler=FlaskSessionCacheHandler(session),
    show_dialog=True
)
sp = Spotify(auth_manager=sp_oauth)

def is_valid_token():
    token_info = sp_oauth.get_cached_token()

    if not token_info:
        return False

    if sp_oauth.is_token_expired(token_info):
        try:
            sp_oauth.refresh_access_token(token_info['refresh_token'])
            return True
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return False

    return True
