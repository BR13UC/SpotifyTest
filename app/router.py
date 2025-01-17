from flask import Blueprint, redirect, url_for, request, render_template
from app.helpers import sp_oauth, is_valid_token

router_bp = Blueprint('router', __name__)

@router_bp.route('/')
def redirect_to_auth():
    if not is_valid_token():
        return redirect(url_for('auth.home'))
    return redirect(url_for('router.home'))

@router_bp.route('/home')
def home():
    if not is_valid_token():
        return redirect(url_for('auth.authenticate'))
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
    try:
        print("Request args:", request.args)
        code = request.args.get('code')
        print("Authorization code:", code)
        token_info = sp_oauth.get_access_token(code)
        print("Token info:", token_info)
        return redirect(url_for('router.home'))
    except Exception as e:
        print("Error in callback:", e)
        return "Authorization failed"