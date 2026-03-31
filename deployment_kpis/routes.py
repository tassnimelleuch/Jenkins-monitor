from flask import session, jsonify, render_template
from deployment_kpis import deployment_kpis_bp
from models import get_pending_count
from services.access_service import role_required
from services.deployment_kpis_service import get_deployment_kpis

@deployment_kpis_bp.route('/deployment_kpis')
@role_required('admin', 'dev', 'qa')
def deployment_kpis():
    return render_template(
        'deployment_kpis.html',
        username=session.get('username'),
        role=session.get('role')
    )


@deployment_kpis_bp.route('/deployment_kpis/api/cluster')
@role_required('admin', 'dev', 'qa')
def deployment_kpis_cluster():
    result = get_deployment_kpis()
    status_code = 200 if result.get('connected') else 503
    return jsonify(result), status_code
