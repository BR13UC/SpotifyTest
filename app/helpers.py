import os
from flask import session
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler
from dotenv import load_dotenv
import networkx as nx
from db_connection import get_collection

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

import networkx as nx

def export_artist_genre_graphml(output_file="static/artist_genre.graphml"):
    G = nx.Graph()
    collection = get_collection("followed_artists")
    followed_artists_data = collection.find_one()

    if not followed_artists_data:
        print("No followed artists data.")
        return

    artists = followed_artists_data.get("artists", [])
    for artist in artists:
        artist_name = artist.get("name", "Unknown Artist")
        genres = artist.get("genres", [])
        for genre in genres:
            G.add_node(artist_name, type="artist")
            G.add_node(genre, type="genre")
            G.add_edge(artist_name, genre)

    nx.write_graphml(G, output_file)
    print(f"Exported artist-genre data to {output_file}")
