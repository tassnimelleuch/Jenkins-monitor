from flask import session, jsonify, render_template
from overview import overview_bp
from services.access_service import dashboard_user_required
from services.jenkins_service import get_kpis
from collectors.jenkins_collector import check_connection, get_console_log
from services.azure_service import get_connection_status

@overview_bp.route('/overview')
@dashboard_user_required
def dashboard():
    return render_template(
        'overview.html',
        username=session.get('username'),
        role=session.get('role')
    )


@overview_bp.route('/api/pipeline/kpis')
@dashboard_user_required
def kpis():
    return jsonify(get_kpis())


@overview_bp.route('/api/status')
@dashboard_user_required
def status():
    return jsonify({'connected': check_connection()})


@overview_bp.route('/api/log/<int:build_number>')
@dashboard_user_required
def log_api(build_number):
    log = get_console_log(build_number)
    return jsonify({'log': log, 'build_number': build_number})


@overview_bp.route('/console/<int:build_number>')
@dashboard_user_required
def console(build_number):
    return render_template(
        'console.html',
        build_number=build_number,
        username=session.get('username'),
        role=session.get('role')
    )

@overview_bp.route('/api/latest_build')
@dashboard_user_required
def latest_build():
    kpis = get_kpis()
    return jsonify({
        'build_number': kpis.get('last_build_number')
    })

@overview_bp.route('/api/azure/status', methods=['GET'])
def azure_status():
    result = get_connection_status()
    status_code = 200 if result['connected'] else 503
    return jsonify(result), status_code
