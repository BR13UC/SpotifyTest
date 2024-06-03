import os
from flask import Flask, request, redirect, session, url_for, render_template, jsonify

from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(64)

client_id = '9aea088859ff4cd184e19e577c24da52'
client_secret = 'a7d5c277a48b4ce2906ce0d6292a3d6c'
redirect_uri = 'http://localhost:5000/callback'
scope = 'playlist-read-private playlist-modify-public playlist-modify-private'

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

@app.route('/')
def home():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        auth_url = sp_oauth.get_authorize_url()
        return redirect(auth_url)
    return render_template('index.html')

@app.route('/callback')
def callback():
    sp_oauth.get_access_token(request.args['code'])
    return redirect(url_for('home'))

@app.route('/get_playlists', methods=['GET'])
def get_playlists():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        return jsonify({'error': 'Not authenticated'}), 401

    playlists = sp.current_user_playlists()
    playlist_options = [{'id': pl['id'], 'name': pl['name']} for pl in playlists['items']]
    return jsonify(playlists=playlist_options)

@app.route('/get_playlist_tracks', methods=['POST'])
def get_playlist_tracks():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    playlist_id = data['playlist_id']
    sort_option = data.get('sort_option', 'length_asc')
    
    offset = 0
    track_titles = []

    while True:
        tracks = sp.playlist_tracks(playlist_id, offset=offset)
        track_titles.extend([track['track']['name'] for track in tracks['items']])
        if not tracks['next']:
            break
        offset += len(tracks['items'])

    if sort_option == 'length_asc':
        sorted_track_titles = sorted(track_titles, key=len)
    elif sort_option == 'length_desc':
        sorted_track_titles = sorted(track_titles, key=len, reverse=True)
    elif sort_option == 'name_asc':
        sorted_track_titles = sorted(track_titles)
    elif sort_option == 'name_desc':
        sorted_track_titles = sorted(track_titles, reverse=True)
    else:
        sorted_track_titles = track_titles

    return jsonify({'sorted_titles': sorted_track_titles})


@app.route('/get_profile', methods=['GET'])
def get_profile():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        return jsonify({'error': 'Not authenticated'}), 401

    user_profile = sp.current_user()
    return jsonify(user_profile)

@app.route('/get_sort_options', methods=['GET'])
def get_sort_options():
    sort_options = [
        {'id': 'name_asc', 'name': 'Name Ascending'},
        {'id': 'name_desc', 'name': 'Name Descending'},
        {'id': 'length_asc', 'name': 'Length Ascending'},
        {'id': 'length_desc', 'name': 'Length Descending'},
    ]
    return jsonify(sort_options)

@app.route('/create_sorted_playlist', methods=['POST'])
def create_sorted_playlist():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        return jsonify({'error': 'Not authenticated'}), 401

    track_titles = request.json.get('track_titles', [])

    track_ids = []
    for title in track_titles:
        results = sp.search(q=f'track:{title}', type='track', limit=1)
        items = results['tracks']['items']

        if items:
            track_ids.append(items[0]['id'])
        else:
            print(f"No track found for title: {title}")

    if not track_ids:
        return 'No tracks found for the provided titles.', 400

    user_id = sp.current_user()['id']
    new_playlist = sp.user_playlist_create(user_id, 'Sorted Playlist')

    chunk_size = 100
    for i in range(0, len(track_ids), chunk_size):
        chunk = track_ids[i:i + chunk_size]
        sp.playlist_add_items(new_playlist['id'], chunk)

    return 'New playlist created with sorted track titles'

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
