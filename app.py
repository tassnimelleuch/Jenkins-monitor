from flask import Flask, jsonify, redirect, request, url_for, session
from auth import auth_bp
from alerts import alerts_bp
from overview import overview_bp
from user_management import user_management_bp
from config import Config
from pipeline_kpis import pipeline_kpis_bp

from deployment_kpis import deployment_kpis_bp
from sonarcloud import sonarcloud_bp
from github import github_bp
from finops import finops_bp
from ecoops import ecoops_bp
from chatbot import chatbot_bp
from extensions import cache, db
from pipeline_identity import configured_branch_name, pipeline_name
from services.user_account_service import (
    ensure_admin_account,
    get_active_session_user,
    get_pending_count,
    normalize_role,
    role_matches,
)
from services.access_service import build_access_context
from services.alerts_service import get_open_alert_count
from services.system_timezone_service import get_system_timezone_name
from services.background_refresh_service import start_live_refresh_worker

app = Flask(__name__)
app.config.from_object(Config)
cache.init_app(app)
db.init_app(app)
app.secret_key = app.config['SECRET_KEY']

from alerts_models import PersistentAlert
from auth_models import UserAccount
from dashboard_kpi_documents_models import (
    DashboardKpiDocument,
    DashboardKpiDocumentChunk,
)
from finops_models import (
    FinOpsBuildDocument,
    FinOpsDailyCost,
    FinOpsSyncState,
    ensure_finops_storage_schema,
)
from github_storage_models import (
    GitHubCommit,
    GitHubCommitFile,
    GitHubRepoSyncState,
    ensure_github_storage_schema,
)
from pipeline_storage_models import (
    PipelineBranch,
    PipelineMainBuild,
    PipelineMainBuildStage,
    ensure_pipeline_storage_schema,
)
with app.app_context():
    db.create_all()
    ensure_finops_storage_schema()
    ensure_github_storage_schema()
    ensure_pipeline_storage_schema()
    ensure_admin_account()

app.register_blueprint(auth_bp)
app.register_blueprint(alerts_bp)
app.register_blueprint(overview_bp)
app.register_blueprint(pipeline_kpis_bp)
app.register_blueprint(user_management_bp)
app.register_blueprint(deployment_kpis_bp)
app.register_blueprint(sonarcloud_bp)
app.register_blueprint(github_bp)
app.register_blueprint(finops_bp)
app.register_blueprint(ecoops_bp)
app.register_blueprint(chatbot_bp)

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
    branch_name = configured_branch_name(app.config, default='main')
    current_user = None
    current_role = session.get('role')
    if session.get('username'):
        current_user = get_active_session_user(session.get('username'))
        if current_user:
            current_role = normalize_role(current_user.role)
            session['role'] = current_role
    access = build_access_context(current_role)
    context = {
        'pipeline_name': pipeline_name(app.config.get('JENKINS_JOB'), branch_name=branch_name),
        'branch_name': branch_name,
        'system_time_zone': get_system_timezone_name(),
        'access': access,
        'has_role': lambda *roles, role=current_role: role_matches(role, roles),
    }
    if access['manage_users']:
        context['pending_count'] = get_pending_count()
        try:
            context['open_alert_count'] = get_open_alert_count()
        except Exception:
            context['open_alert_count'] = 0
    return context


@app.before_request
def ensure_live_refresh_worker():
    start_live_refresh_worker(app)


if __name__ == '__main__':
    app.run(debug=True)
