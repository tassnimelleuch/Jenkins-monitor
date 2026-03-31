from flask import session, jsonify, render_template
from overview import overview_bp
from services.access_service import role_required
from services.dashboard_service import get_kpis
from providers.jenkins import check_connection, get_console_log
from services.azure_service import get_connection_status

@overview_bp.route('/overview')
@role_required('admin', 'dev', 'qa')
def dashboard():
    return render_template(
        'overview.html',
        username=session.get('username'),
        role=session.get('role')
    )


@overview_bp.route('/api/kpis')
@role_required('admin', 'dev', 'qa')
def kpis():
    return jsonify(get_kpis())


@overview_bp.route('/api/status')
@role_required('admin', 'dev', 'qa')
def status():
    return jsonify({'connected': check_connection()})


@overview_bp.route('/api/log/<int:build_number>')
@role_required('admin', 'dev', 'qa')
def log_api(build_number):
    log = get_console_log(build_number)
    return jsonify({'log': log, 'build_number': build_number})


@overview_bp.route('/console/<int:build_number>')
@role_required('admin', 'dev', 'qa')
def console(build_number):
    return render_template(
        'console.html',
        build_number=build_number,
        username=session.get('username'),
        role=session.get('role')
    )

@overview_bp.route('/api/latest_build')
@role_required('admin', 'dev', 'qa')
def latest_build():
    kpis = get_kpis()
    return jsonify({
        'build_number': kpis.get('last_build_number')
    })

@overview_bp.route('/azure/api/status', methods=['GET'])
def azure_status():
    result = get_connection_status()
    status_code = 200 if result['connected'] else 503
    return jsonify(result), status_code