from flask import Flask
from flask_session import Session
import os

print("Starting app package")

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static', static_url_path='/static')
    app.config['SECRET_KEY'] = os.urandom(64)
    app.config['SESSION_TYPE'] = 'filesystem'
    # from app.routes.artists import artist_bp
    Session(app)
    from app.routes.auth import auth_bp
    from app.routes.errors import error_bp
    from app.routes.graph import graph_bp
    from app.routes.playlists import playlist_bp
    from app.routes.sort import sort_bp
    from app.routes.users import user_bp

    from app.router import router_bp

    # app.register_blueprint(artist_bp, url_prefix='/artists')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(error_bp, url_prefix='/errors')
    app.register_blueprint(graph_bp, url_prefix='/graphs')
    app.register_blueprint(playlist_bp, url_prefix='/playlists')
    app.register_blueprint(sort_bp, url_prefix='/sorts')
    app.register_blueprint(user_bp, url_prefix='/users')

    app.register_blueprint(router_bp, url_prefix='/')

    # print(app.url_map)

    return app
