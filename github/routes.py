from flask import jsonify, render_template, session
from github import github_bp
from services.access_service import role_required
from services.github_service import get_github_summary


@github_bp.route('/github')
@role_required('admin', 'dev', 'qa')
def dashboard():
    return render_template(
        'github.html',
        username=session.get('username'),
        role=session.get('role')
    )


@github_bp.route('/api/github')
@role_required('admin', 'dev', 'qa')
def github_api():
    return jsonify(get_github_summary())
