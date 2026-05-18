from flask import session, jsonify, render_template, request
from overview import overview_bp
from services.access_service import role_required
from services.jenkins_service import get_overview_kpis, request_pipeline_background_refresh
from services.pipeline_storage_service import get_stored_overview_kpis
from collectors.jenkins_collector import check_connection, get_console_log
from services.azure_service import get_connection_status

@overview_bp.route('/overview')
@role_required('admin', 'developer', 'tester')
def dashboard():
    return render_template(
        'overview.html',
        username=session.get('username'),
        role=session.get('role'),
        initial_overview_kpis=get_stored_overview_kpis(),
    )


@overview_bp.route('/api/pipeline/kpis')
@role_required('admin', 'developer', 'tester')
def kpis():
    if request.args.get('refresh') == '1':
        request_pipeline_background_refresh()
    return jsonify(get_overview_kpis())


@overview_bp.route('/api/status')
@role_required('admin', 'developer', 'tester')
def status():
    return jsonify({'connected': check_connection()})


@overview_bp.route('/api/log/<int:build_number>')
@role_required('admin', 'developer', 'tester')
def log_api(build_number):
    log = get_console_log(build_number)
    return jsonify({'log': log, 'build_number': build_number})


@overview_bp.route('/console/<int:build_number>')
@role_required('admin', 'developer', 'tester')
def console(build_number):
    return render_template(
        'console.html',
        build_number=build_number,
        username=session.get('username'),
        role=session.get('role')
    )

@overview_bp.route('/api/latest_build')
@role_required('admin', 'developer', 'tester')
def latest_build():
    kpis = get_overview_kpis()
    return jsonify({
        'build_number': kpis.get('last_build_number')
    })

@overview_bp.route('/api/azure/status', methods=['GET'])
@role_required('admin', 'developer', 'tester')
def azure_status():
    result = get_connection_status()
    status_code = 200 if result['connected'] else 503
    return jsonify(result), status_code
