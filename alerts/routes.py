from flask import jsonify, render_template, session

from alerts import alerts_bp
from services.access_service import role_required
from services.alerts_service import get_duration_alerts


@alerts_bp.route('/alerts')
@role_required('admin', 'developer', 'tester')
def alerts_page():
    return render_template(
        'alerts.html',
        username=session.get('username'),
        role=session.get('role'),
    )


@alerts_bp.route('/api/alerts')
@role_required('admin', 'developer', 'tester')
def alerts_api():
    return jsonify(get_duration_alerts())
