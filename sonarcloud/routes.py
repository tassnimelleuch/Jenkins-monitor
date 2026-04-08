from flask import jsonify, render_template, session
from sonarcloud import sonarcloud_bp
from services.access_service import role_required
from services.sonarcloud_service import get_sonarcloud_summary


@sonarcloud_bp.route('/sonarcloud')
@role_required('admin', 'dev', 'qa')
def dashboard():
    return render_template(
        'sonarcloud.html',
        username=session.get('username'),
        role=session.get('role')
    )


@sonarcloud_bp.route('/api/sonarcloud')
@role_required('admin', 'dev', 'qa')
def sonarcloud_api():
    return jsonify(get_sonarcloud_summary())
