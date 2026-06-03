import json

from flask import session, jsonify, render_template, current_app, request
from pipeline_kpis import pipeline_kpis_bp
from services.access_service import (
    admin_required,
    build_abort_required,
    build_trigger_required,
    dashboard_user_required,
)
from services.background_refresh_service import get_dashboard_live_state
from services.jenkins_service import (
    get_pipeline_kpis,
    invalidate_pipeline_live_state,
    refresh_pipeline_storage_from_jenkins,
    request_pipeline_background_refresh,
)
from collectors.jenkins_collector import trigger_build, abort_build
from services.metrics_service import get_vm_metrics
from services.pipeline_storage_service import get_stored_pipeline_kpis
from services.user_account_service import get_pending_count

@pipeline_kpis_bp.route('/pipeline_kpis')
@dashboard_user_required
def pipeline_kpis():
    return render_template(
        'pipeline_kpis.html',
        username=session.get('username'),
        role=session.get('role'),
        pending_count=get_pending_count()
    )


@pipeline_kpis_bp.route('/api/pipeline_kpis')
@dashboard_user_required
def pipeline_kpis_api():
    if request.args.get('refresh') == '1' and request.args.get('wait') == '1':
        payload = refresh_pipeline_storage_from_jenkins(
            include_quality_metrics=False,
            include_quality_backfill=False,
        )
        response_payload = get_stored_pipeline_kpis() or payload
        return current_app.response_class(
            json.dumps(response_payload, indent=2),
            mimetype='application/json'
        )
    if request.args.get('refresh') == '1':
        request_pipeline_background_refresh()
    payload = get_pipeline_kpis()
    return current_app.response_class(
        json.dumps(payload, indent=2),
        mimetype='application/json'
    )


@pipeline_kpis_bp.route('/api/running_stages')
@dashboard_user_required
def running_stages():
    return jsonify(get_dashboard_live_state().get('running_stages') or [])


@pipeline_kpis_bp.route('/api/running_builds')
@dashboard_user_required
def running_builds():
    return jsonify(get_dashboard_live_state().get('running_builds') or [])


@pipeline_kpis_bp.route('/api/build', methods=['POST'])
@build_trigger_required
def build():
    success, message = trigger_build()
    if success:
        invalidate_pipeline_live_state()
        return jsonify({'queued': True, 'message': message})
    return jsonify({'queued': False, 'error': message}), 500


@pipeline_kpis_bp.route('/api/abort/<int:build_number>', methods=['POST'])
@build_abort_required
def abort(build_number):
    success, message = abort_build(build_number)
    if success:
        invalidate_pipeline_live_state()
        return jsonify({'aborted': True, 'message': message})
    return jsonify({'aborted': False, 'error': message}), 500

@pipeline_kpis_bp.route('/api/vm-metrics')
@admin_required
def vm_metrics_api():
    from flask import jsonify
    return jsonify(get_vm_metrics())
