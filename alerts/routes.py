from flask import Response, jsonify, render_template, session, stream_with_context

from alerts import alerts_bp
from services.access_service import role_required
from services.alerts_service import (
    get_alerts_payload,
    mark_persistent_alert_checked,
)
from services.live_stream_service import iter_alert_live_events


@alerts_bp.route('/alerts')
@role_required('admin')
def alerts_page():
    return render_template(
        'alerts.html',
        username=session.get('username'),
        role=session.get('role'),
    )


@alerts_bp.route('/api/alerts')
@role_required('admin')
def alerts_api():
    return jsonify(get_alerts_payload())


@alerts_bp.route('/api/alerts/stream')
@role_required('admin')
def alerts_stream():
    response = Response(
        stream_with_context(iter_alert_live_events()),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


@alerts_bp.route('/api/alerts/<int:alert_id>/check', methods=['POST'])
@role_required('admin')
def check_alert(alert_id):
    row = mark_persistent_alert_checked(alert_id, session.get('username'))
    if row is None:
        return jsonify({'error': 'Alert not found.'}), 404
    return jsonify({
        'checked': True,
        'alert_id': row.id,
        'checked_at': row.checked_at.isoformat() if row.checked_at else None,
        'checked_by': row.checked_by_username,
    }), 200
