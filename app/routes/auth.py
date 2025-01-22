from flask import Blueprint, redirect, session, url_for, render_template
from app.helpers import is_valid_token, sp_oauth

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/')
def home():
    if not is_valid_token():
        auth_url = sp_oauth.get_authorize_url()
        return redirect(auth_url)
    return render_template('index.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.home'))