from flask import Flask, redirect, url_for
from blueprints import auth_bp

def create_app():
    app = Flask(__name__, 
                template_folder='../templates',
                static_folder='../static')
    
    app.config['SECRET_KEY'] = 'dev-secret'
    
    @app.route('/')
    def home():
        return redirect(url_for('auth.login'))
    
    app.register_blueprint(auth_bp)

    return app