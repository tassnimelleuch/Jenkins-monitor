import json

from flask import session, jsonify, render_template, current_app
from pipeline_kpis import pipeline_kpis_bp
from services.access_service import role_required
from services.jenkins_service import (
    get_live_running_builds,
    get_pipeline_kpis,
    invalidate_pipeline_live_state,
)
from collectors.jenkins_collector import trigger_build, abort_build
from services.metrics_service import get_vm_metrics
from services.user_account_service import get_pending_count

@pipeline_kpis_bp.route('/pipeline_kpis')
@role_required('admin', 'developer', 'tester')
def pipeline_kpis():
    return render_template(
        'pipeline_kpis.html',
        username=session.get('username'),
        role=session.get('role'),
        pending_count=get_pending_count()
    )


@pipeline_kpis_bp.route('/api/pipeline_kpis')
@role_required('admin', 'developer', 'tester')
def pipeline_kpis_api():
    payload = get_pipeline_kpis()
    return current_app.response_class(
        json.dumps(payload, indent=2),
        mimetype='application/json'
    )


@pipeline_kpis_bp.route('/api/running_stages')
@role_required('admin', 'developer', 'tester')
def running_stages():
    return jsonify(get_live_running_builds())


@pipeline_kpis_bp.route('/api/build', methods=['POST'])
@role_required('admin', 'developer')
def build():
    success, message = trigger_build()
    if success:
        invalidate_pipeline_live_state()
        return jsonify({'queued': True, 'message': message})
    return jsonify({'queued': False, 'error': message}), 500


@pipeline_kpis_bp.route('/api/abort/<int:build_number>', methods=['POST'])
@role_required('admin', 'developer')
def abort(build_number):
    success, message = abort_build(build_number)
    if success:
        invalidate_pipeline_live_state()
        return jsonify({'aborted': True, 'message': message})
    return jsonify({'aborted': False, 'error': message}), 500

@pipeline_kpis_bp.route('/api/vm-metrics')
@role_required('admin')
def vm_metrics_api():
    from flask import jsonify
    return jsonify(get_vm_metrics())
