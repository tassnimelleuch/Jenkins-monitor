from flask import Flask, redirect, url_for, session

from auth import auth_bp
from overview import overview_bp
from pipeline_kpis import pipeline_kpis_bp
from user_management import user_management_bp
from config import Config
from deployment_kpis import deployment_kpis_bp
from sonarcloud import sonarcloud_bp
from github import github_bp
from models import get_pending_count

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

app.register_blueprint(auth_bp)
app.register_blueprint(overview_bp, url_prefix='/jenkins')
app.register_blueprint(pipeline_kpis_bp, url_prefix='/jenkins')
app.register_blueprint(user_management_bp)
app.register_blueprint(deployment_kpis_bp, url_prefix='/jenkins')
app.register_blueprint(sonarcloud_bp)
app.register_blueprint(github_bp)

@app.route('/')
def home():
    if session.get('role') in ('admin', 'dev', 'qa'):
        return redirect(url_for('overview.dashboard'))
    return redirect(url_for('auth.login'))

@app.context_processor
def inject_pending_count():
    if session.get('role') == 'admin':
        return {'pending_count': get_pending_count()}
    return {}

if __name__ == '__main__':
    app.run(debug=True)
