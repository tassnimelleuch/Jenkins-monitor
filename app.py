from flask import Flask, redirect, url_for, session

from auth import auth_bp
from overview import overview_bp
from pipeline_kpis import pipeline_kpis_bp
from user_management import user_management_bp
from config import Config

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

app.register_blueprint(auth_bp)
app.register_blueprint(overview_bp, url_prefix='/jenkins')
app.register_blueprint(pipeline_kpis_bp, url_prefix='/jenkins')
app.register_blueprint(user_management_bp)

@app.route('/')
def home():
    if session.get('role') in ('admin', 'dev', 'qa'):
        return redirect(url_for('overview.dashboard'))
    return redirect(url_for('auth.login'))

if __name__ == '__main__':
    app.run(debug=True)
