from flask import session, jsonify, render_template
from pipeline_kpis import pipeline_kpis_bp
from services.access_service import role_required
from services.dashboard_service import get_pipeline_kpis
from providers.jenkins import get_running_stages, trigger_build, abort_build


@pipeline_kpis_bp.route('/pipeline_kpis')
@role_required('admin', 'dev', 'qa')
def pipeline_kpis():
    return render_template(
        'pipeline_kpis.html',
        username=session.get('username'),
        role=session.get('role'),
        pending_count=0
    )


@pipeline_kpis_bp.route('/api/pipeline_kpis')
@role_required('admin', 'dev', 'qa')
def pipeline_kpis_api():
    return jsonify(get_pipeline_kpis())


@pipeline_kpis_bp.route('/api/running_stages')
@role_required('admin', 'dev', 'qa')
def running_stages():
    return jsonify(get_running_stages())


@pipeline_kpis_bp.route('/api/build', methods=['POST'])
@role_required('admin')
def build():
    success, message = trigger_build()
    if success:
        return jsonify({'queued': True, 'message': message})
    else:
        return jsonify({'queued': False, 'error': message}), 500


@pipeline_kpis_bp.route('/api/abort/<int:build_number>', methods=['POST'])
@role_required('admin')
def abort(build_number):
    success, message = abort_build(build_number)
    if success:
        return jsonify({'aborted': True, 'message': message})
    else:
        return jsonify({'aborted': False, 'error': message}), 500