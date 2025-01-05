import os
from flask import session
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler
from dotenv import load_dotenv
import networkx as nx
from app.db_connection import get_collection
import networkx as nx

load_dotenv()

sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope='user-follow-read playlist-read-private playlist-modify-public playlist-modify-private',
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

def get_genre_color_map():
    genre_colors = {
        'pop': '#ff0000',
        'rock': '#00ff00',
        'jazz': '#0000ff',
        'hip hop': '#ff00ff',
        'classical': '#ffff00',
        'country': '#00ffff',
    }
    return genre_colors

def get_artist_color(genres, genre_colors):
    if len(genres) == 1:
        return genre_colors.get(genres[0], '#cccccc')
    r, g, b = 0, 0, 0
    for genre in genres:
        color = genre_colors.get(genre, '#cccccc')
        r += int(color[1:3], 16)
        g += int(color[3:5], 16)
        b += int(color[5:7], 16)
    r, g, b = r // len(genres), g // len(genres), b // len(genres)
    return f'#{r:02x}{g:02x}{b:02x}'
