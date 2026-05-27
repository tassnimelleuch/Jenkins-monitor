from flask import Response, jsonify, render_template, request, session, stream_with_context
from sonarcloud import sonarcloud_bp
from services.access_service import role_required
from services.background_refresh_service import get_cached_sonarcloud_payload
from services.live_stream_service import iter_sonarcloud_live_events
from services.sonarcloud_service import (
    get_bug_details,
    get_issue_details,
)


@sonarcloud_bp.route('/sonarcloud')
@role_required('admin', 'developer', 'tester')
def dashboard():
    return render_template(
        'sonarcloud.html',
        username=session.get('username'),
        role=session.get('role'),
    )


@sonarcloud_bp.route('/api/sonarcloud')
@role_required('admin', 'developer', 'tester')
def sonarcloud_api():
    result = get_cached_sonarcloud_payload()
    status_code = 200 if result.get('connected') else 503
    return jsonify(result), status_code


@sonarcloud_bp.route('/api/sonarcloud/stream')
@role_required('admin', 'developer', 'tester')
def sonarcloud_live_stream():
    response = Response(
        stream_with_context(iter_sonarcloud_live_events()),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


@sonarcloud_bp.route('/api/sonarcloud/bugs')
@role_required('admin', 'developer', 'tester')
def sonarcloud_bug_details_api():
    level = request.args.get('level')  # low, medium, high
    page = request.args.get('page', default=1, type=int)
    page_size = request.args.get('page_size', default=20, type=int)

    return jsonify(get_bug_details(level=level, page=page, page_size=page_size))


@sonarcloud_bp.route('/api/sonarcloud/issues')
@role_required('admin', 'developer', 'tester')
def sonarcloud_issues_api():
    issue_type = request.args.get('type')  # BUG, VULNERABILITY, CODE_SMELL, SECURITY_HOTSPOT
    severity = request.args.get('severity')
    page = request.args.get('page', default=1, type=int)
    page_size = request.args.get('page_size', default=20, type=int)

    return jsonify(get_issue_details(issue_type=issue_type, severity=severity, page=page, page_size=page_size))
