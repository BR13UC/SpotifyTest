from flask import Blueprint, redirect, url_for, request, render_template, session, jsonify
from app.helpers import is_valid_token, create_spotify_oauth
from spotipy import Spotify

router_bp = Blueprint('router', __name__)

@router_bp.route('/')
def redirect_to_auth():
    if not is_valid_token():
        return redirect(url_for('auth.home'))
    return redirect(url_for('router.home'))

@router_bp.route('/home')
def home():
    if not is_valid_token():
        return redirect(url_for('auth.home'))
    return render_template('index.html')

@router_bp.route('/sorts')
def sort_page():
    if not is_valid_token():
        return redirect(url_for('auth.authenticate'))
    return render_template('sorts.html')

@router_bp.route('/graphs')
def graphs_page():
    if not is_valid_token():
        return redirect(url_for('auth.authenticate'))
    return render_template('graphs.html')

@router_bp.route('/callback')
def callback():
    sp_oauth = create_spotify_oauth()
    code = request.args.get('code')

    try:
        token_info = sp_oauth.get_access_token(code)
        session['spotify_token'] = token_info

        sp = Spotify(auth=token_info['access_token'])
        user_profile = sp.current_user()
        session['user_id'] = user_profile['id']
        session['display_name'] = user_profile['display_name']

        return redirect(url_for('auth.home'))
    except Exception as e:
        print(f"Error during callback: {e}")
        return jsonify({'error': 'Authentication failed'}), 400
