import os
from flask import Flask, request, redirect, session, url_for

from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import FlaskSessionCacheHandler

app = Flask(__name__)
app.config ['SECRET_KEY'] = os.urandom(64)

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

@app.route('/')
def home():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        auth_url = sp_oauth.get_authorize_url()
        return redirect(auth_url)
    return redirect(url_for('choose_action'))

@app.route('/callback')
def callback():
    sp_oauth.get_access_token(request.args['code'])
    return redirect(url_for('choose_action'))

@app.route('/choose_action', methods=['GET', 'POST'])
def choose_action():
    if not sp_oauth.validate_token(cache_handler.get_cached_token()):
        auth_url = sp_oauth.get_authorize_url()
        return redirect(auth_url)

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'display_all':
            return redirect(url_for('display_all_playlists'))
        elif action == 'display_titles':
            return redirect(url_for('display_playlist_titles'))

    return '''
        <form method="post">
            <input type="submit" name="action" value="display_all">
            <input type="submit" name="action" value="display_titles">
        </form>
    '''

@app.route('/display_all_playlists')
def display_all_playlists():
    playlists = sp.current_user_playlists()
    playlists_infos = [(pl['name'], pl['external_urls']['spotify']) for pl in playlists['items']]
    playlists_html = '<br>'.join([f'{name}: {url}' for name, url in playlists_infos])
    return playlists_html

@app.route('/display_playlist_titles', methods=['GET', 'POST'])
def display_playlist_titles():
    if request.method == 'GET':
        playlists = sp.current_user_playlists()
        playlist_options = [(pl['id'], pl['name']) for pl in playlists['items']]

        playlist_options_html = ''.join([f'<option value="{playlist[0]}">{playlist[1]}</option>' for playlist in playlist_options])

        return f'''
            <form method="post">
                <label for="playlist">Choose a playlist:</label>
                <select name="playlist" id="playlist">
                    {playlist_options_html}
                </select>
                <input type="submit" value="Display Titles">
            </form>
        '''
    elif request.method == 'POST':
        playlist_id = request.form.get('playlist')
        offset = 0
        track_titles = []

        while True:
            tracks = sp.playlist_tracks(playlist_id, offset=offset)
            track_titles.extend([track['track']['name'] for track in tracks['items']])
            if not tracks['next']:
                break
            offset += len(tracks['items'])

        sorted_track_titles = sorted(track_titles, key=len)
        track_titles_html = '<br>'.join(sorted_track_titles)

        return f'''
            <h2>Titles of the selected playlist (sorted alphabetically):</h2>
            {track_titles_html}
        '''


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
