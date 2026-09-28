from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    app.run(host=os.environ.get('STATIFY_HOST', '127.0.0.1'), debug=os.environ.get('STATIFY_DEBUG') == '1')
