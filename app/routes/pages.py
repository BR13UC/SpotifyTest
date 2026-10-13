from flask import Blueprint, jsonify, redirect, render_template, session, url_for
from app.spotify_auth import is_valid_token
from app import storage
from app.services import mb_queue

router_bp = Blueprint('router', __name__)

@router_bp.route('/')
def index():
    return redirect(url_for('router.home') if session.get('spotify_user_id') else url_for('auth.home'))

@router_bp.route('/home')
def home():
    if not session.get('spotify_user_id'):
        return redirect(url_for('auth.home'))
    return render_template('index.html')

@router_bp.route('/sorts')
def sort_page():
    if not session.get('spotify_user_id'):
        return redirect(url_for('auth.home'))
    return render_template('sorts.html')

@router_bp.route('/graphs')
def graphs_page():
    if not is_valid_token():
        return redirect(url_for('auth.home'))
    return render_template('graphs.html')


@router_bp.route('/map')
def map_page():
    if not is_valid_token():
        return redirect(url_for('auth.home'))
    return render_template('map.html')


@router_bp.route('/timeline')
def timeline_page():
    if not is_valid_token():
        return redirect(url_for('auth.home'))
    return render_template('timeline.html')


@router_bp.route('/progress')
def progress_page():
    if not is_valid_token():
        return redirect(url_for('auth.home'))
    return render_template('progress.html')


@router_bp.route('/settings')
def settings_page():
    if not session.get('spotify_user_id'):
        return redirect(url_for('auth.home'))
    return render_template('settings.html')


@router_bp.get('/home/summary')
def home_summary():
    if not session.get('spotify_user_id'):
        return jsonify(error='Session Spotify expirée. Reconnectez-vous.', error_code='session_expired'), 401
    user_id = session['spotify_user_id']
    playlists, _ = storage.get_cache(user_id, 'playlists')
    artists, _ = storage.get_cache(user_id, 'followed_artists')
    progress = mb_queue.progress_for(user_id)
    return jsonify(playlists=len(playlists) if playlists is not None else None,
                   artists=artists.get('total', 0) if artists is not None else None,
                   tracks=progress['completed'] if progress['total'] else None)
