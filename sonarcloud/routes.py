from flask import jsonify, render_template, request, session
from sonarcloud import sonarcloud_bp
from services.access_service import role_required
from services.sonarcloud_service import (
    get_sonarcloud_summary,
    get_bug_details,
    get_issue_details,
)


@sonarcloud_bp.route('/sonarcloud')
@role_required('admin', 'dev', 'qa')
def dashboard():
    return render_template(
        'sonarcloud.html',
        username=session.get('username'),
        role=session.get('role'),
    )


@sonarcloud_bp.route('/api/sonarcloud')
@role_required('admin', 'dev', 'qa')
def sonarcloud_api():
    return jsonify(get_sonarcloud_summary())


@sonarcloud_bp.route('/api/sonarcloud/bugs')
@role_required('admin', 'dev', 'qa')
def sonarcloud_bug_details_api():
    level = request.args.get('level')  # low, medium, high
    page = request.args.get('page', default=1, type=int)
    page_size = request.args.get('page_size', default=20, type=int)

    return jsonify(get_bug_details(level=level, page=page, page_size=page_size))


@sonarcloud_bp.route('/api/sonarcloud/issues')
@role_required('admin', 'dev', 'qa')
def sonarcloud_issues_api():
    issue_type = request.args.get('type')  # BUG, VULNERABILITY, CODE_SMELL, SECURITY_HOTSPOT
    severity = request.args.get('severity')
    page = request.args.get('page', default=1, type=int)
    page_size = request.args.get('page_size', default=20, type=int)

    return jsonify(get_issue_details(issue_type=issue_type, severity=severity, page=page, page_size=page_size))
