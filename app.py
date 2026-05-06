from flask import Flask, jsonify, redirect, request, url_for, session
from auth import auth_bp
from overview import overview_bp
from pipeline_kpis import pipeline_kpis_bp
from user_management import user_management_bp
from config import Config
from deployment_kpis import deployment_kpis_bp
from sonarcloud import sonarcloud_bp
from github import github_bp
from finops import finops_bp
from extensions import cache, db
from services.user_account_service import (
    ensure_admin_account,
    get_active_session_user,
    get_pending_count,
    normalize_role,
    role_matches,
)
from services.access_service import can_view_chart

app = Flask(__name__)
app.config.from_object(Config)
cache.init_app(app)
db.init_app(app)
app.secret_key = app.config['SECRET_KEY']

from auth_models import UserAccount
from pipeline_storage_models import PipelineBuildDuration, PipelineStageDuration
with app.app_context():
    db.create_all()
    ensure_admin_account()

app.register_blueprint(auth_bp)
app.register_blueprint(overview_bp)
app.register_blueprint(pipeline_kpis_bp)
app.register_blueprint(user_management_bp)
app.register_blueprint(deployment_kpis_bp)
app.register_blueprint(sonarcloud_bp)
app.register_blueprint(github_bp)
app.register_blueprint(finops_bp)


def _display_pipeline_name(job_path, branch_name=None):
    raw_job = (job_path or '').strip().strip('/')
    if not raw_job:
        return 'Jenkins Pipeline'

    normalized = raw_job.replace('/job/', '/')
    if normalized.startswith('job/'):
        normalized = normalized[4:]

    parts = [part for part in normalized.split('/') if part]
    if branch_name and len(parts) > 1 and parts[-1] == branch_name:
        parts = parts[:-1]
    return parts[-1] if parts else raw_job


@app.route('/')
def home():
    if session.get('username'):
        current_user = get_active_session_user(session.get('username'))
        if current_user and role_matches(current_user.role, ('admin', 'developer', 'tester')):
            session['role'] = normalize_role(current_user.role)
            return redirect(url_for('overview.dashboard'))
        session.clear()
    return redirect(url_for('auth.login'))


@app.context_processor
def inject_pending_count():
    branch_name = (app.config.get('JENKINS_BRANCH') or 'main').strip() or 'main'
    current_role = session.get('role')
    context = {
        'pipeline_name': _display_pipeline_name(app.config.get('JENKINS_JOB'), branch_name),
        'branch_name': branch_name,
        'can_view_chart': lambda chart_key, role=current_role: can_view_chart(role, chart_key),
    }
    if current_role == 'admin':
        context['pending_count'] = get_pending_count()
    return context


if __name__ == '__main__':
    app.run(debug=True)
