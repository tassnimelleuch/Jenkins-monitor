from flask import Response, current_app, jsonify, render_template, session, stream_with_context

from alerts import alerts_bp
from services.access_service import admin_required
from services.background_refresh_service import (
    get_cached_alerts_payload,
    refresh_alerts_live_state,
)
from services.alerts_service import (
    mark_persistent_alert_checked,
)
from services.live_stream_service import iter_alert_live_events


@alerts_bp.route('/alerts')
@admin_required
def alerts_page():
    return render_template(
        'alerts.html',
        username=session.get('username'),
        role=session.get('role'),
    )


@alerts_bp.route('/api/alerts')
@admin_required
def alerts_api():
    return jsonify(get_cached_alerts_payload())


@alerts_bp.route('/api/alerts/stream')
@admin_required
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
@admin_required
def check_alert(alert_id):
    row = mark_persistent_alert_checked(alert_id, session.get('username'))
    if row is None:
        return jsonify({'error': 'Alert not found.'}), 404
    try:
        refresh_alerts_live_state(force=True, refresh_pipeline_snapshot=False)
    except Exception:
        current_app.logger.exception(
            'Failed to refresh cached alerts after marking alert %s as checked.',
            alert_id,
        )
    return jsonify({
        'checked': True,
        'alert_id': row.id,
        'checked_at': row.checked_at.isoformat() if row.checked_at else None,
        'checked_by': row.checked_by_username,
    }), 200
