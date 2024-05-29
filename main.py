import os

from flask import Flask, request, redirect, session, url_for

from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler

from song_model import Song
from db import create_tables

from song_dao import dao_get_all_songs, dao_save_songs

from typing import List

# app = Flask(__name__)
# app.config ['SECRET_KEY'] = os.urandom(64)

client_id = '9aea088859ff4cd184e19e577c24da52'
client_secret = 'a7d5c277a48b4ce2906ce0d6292a3d6c'
redirect_uri = 'http://localhost:5000/callback'
scope = 'playlist-read-private'

cache_handler = FlaskSessionCacheHandler(session)
sp_oauth = SpotifyOAuth(
    client_id=client_id,
    client_secret=client_secret,
    redirect_uri=redirect_uri,
    scope=scope,
    cache_handler=cache_handler,
    show_dialog=True
)

sp = Spotify(auth_manager=sp_oauth)

# @app.route('/')
# def home():
#     if not sp_oauth.validate_token(cache_handler.get_cached_token()):
#         auth_url = sp_oauth.get_authorize_url()
#         return redirect(auth_url)
#     # return redirect(url_for('search_song'))
#     return redirect(url_for('get_playlists'))

# @app.route('/callback')
# def callback():
#     sp_oauth.get_access_token(request.args['code'])
#     # return redirect(url_for('search_song'))
#     return redirect(url_for('get_playlists'))

# @app.route('/get_playlists')
# def get_playlists():
#     if not sp_oauth.validate_token(cache_handler.get_cached_token()):
#         auth_url = sp_oauth.get_authorize_url()
#         return redirect(auth_url)

#     playlists = sp.current_user_playlists()
#     playlists_infos = [(pl['name'], pl['external_urls']['spotify']) for pl in playlists['items']]
#     playlists_html = '<br>'.join([f'{name}: {url}' for name, url in playlists_infos])
#     return playlists_html

# @app.route('/logout')
# def logout():
#     session.clear()
#     return redirect(url_for('home'))

def search_songs(query: str) -> List[Song]:
    results = sp.search(query, limit=10)
    songs = []
    for track in results["tracks"]["items"]:
        song = Song(
            title=track["name"],
            artist=track["artists"][0]["name"],
            album=track["album"]["name"],
            spotify_id=track["id"]
        )
        songs.append(song)
    return songs

if __name__ == '__main__':
    # app.run(debug=True)
    create_tables()

    while True:
        selection = input('''
            Enter:
            s - search
            p - print
            q - quit
        ''')

        selection = selection.lower()

        if selection == 'p':
            print("songs: ")
            all_songs= dao_get_all_songs()
            for song in all_songs:
                print(f"Title: {song.title} Artist: {song.artist} Album: {song.album}")

        elif selection == 's':
            search_query = input("enter your search: ")
            songs = search_songs(search_query)

            if len(songs) > 0:
                print(f"Songs returned: {len(songs)}")
                for i, song in enumerate(songs, start=1):
                    print(f"{i}: Title {song.title} Artist: {song.artist}")
                save_choice = input("save (y/n): ")
                if save_choice.lower() == 'y':
                    dao_save_songs(songs)
                else:
                    print("songs not saved")
            else:
                print("no songs found")

        elif selection == 'q':
            break