"""Local UI smoke server with simulated Spotify; no real account or credentials."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'tests'))

from flask import redirect, session
from app import create_app, spotify_auth, storage
from app.routes import pages
from test_artist_sources import CollectionSpotify, seed

app = create_app({'TESTING': True, 'SECRET_KEY': 'ui-smoke-test', 'DATA_DIR': str(root / 'work' / 'source-ui-data')})
spotify = CollectionSpotify()
spotify_auth.get_spotify = lambda: spotify
spotify_auth.is_valid_token = lambda: bool(session.get('spotify_user_id'))
pages.is_valid_token = spotify_auth.is_valid_token
with app.app_context():
    loaded, _ = storage.get_cache("alice", "followed_artists")
if loaded is None:
    seed(app)

with app.app_context():
    storage.set_cache('alice', 'musicbrainz_attempt_stats', {'attempts': 10, 'failed': 1, 'since': storage.now()}, 'recording')
    storage.set_cache('alice', 'musicbrainz_attempt_stats', {'attempts': 4, 'failed': 2, 'since': storage.now()}, 'artist')

@app.get('/__test_login')
def test_login():
    session['spotify_user_id'] = 'alice'
    session['csrf_token'] = 'test'
    return redirect('/graphs')

app.run(host='0.0.0.0', port=5097, debug=False)
