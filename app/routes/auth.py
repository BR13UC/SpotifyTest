import hmac
import secrets
from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, current_app, redirect, request, session, url_for

from app import spotify_auth

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/')
def home():
    callback_url = urlsplit(current_app.config['SPOTIPY_REDIRECT_URI'])
    if request.host.split(':', 1)[0] != callback_url.hostname or request.scheme != callback_url.scheme:
        return redirect(urlunsplit((callback_url.scheme, callback_url.netloc, url_for('auth.home'), '', '')))
    if spotify_auth.is_valid_token():
        return redirect(url_for('router.home'))
    session.clear()
    session['oauth_state'] = secrets.token_urlsafe(32)
    return redirect(spotify_auth.get_oauth().get_authorize_url(state=session['oauth_state']))

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.home'))


@auth_bp.route('/callback')
def callback():
    expected = session.pop('oauth_state', '')
    supplied = request.args.get('state', '')
    code = request.args.get('code')
    if not expected or not hmac.compare_digest(expected, supplied) or not code:
        session.clear()
        return 'Autorisation Spotify invalide. Recommencez depuis la page de connexion.', 400
    try:
        spotify_auth.get_oauth().get_access_token(code, check_cache=False)
        profile = spotify_auth.get_spotify().current_user()
        session['spotify_user_id'] = profile['id']
        from app import storage
        storage.set_cache(profile['id'], 'profile', profile)
        session['csrf_token'] = secrets.token_urlsafe(32)
        return redirect(url_for('router.home'))
    except Exception:
        current_app.logger.exception('Spotify callback failed')
        session.clear()
        return 'Autorisation Spotify impossible.', 502
