from flask import session, jsonify, render_template, request
from pipeline_kpis import pipeline_kpis_bp
from services.access_service import role_required
from services.jenkins_service import get_pipeline_kpis
from collectors.jenkins_collector import get_running_stages, trigger_build, abort_build
from models import get_pending_count
from services.metrics_service import get_vm_metrics
from services.pipeline_details_service import get_pipeline_details_summary
from services.pipeline_db_kpis_service import (
    get_kpis_from_database,
    get_recent_builds_from_database,
    get_build_statistics_by_branch,
)

@pipeline_kpis_bp.route('/pipeline_kpis')
@role_required('admin', 'dev', 'qa')
def pipeline_kpis():
    return render_template(
        'pipeline_kpis.html',
        username=session.get('username'),
        role=session.get('role'),
        pending_count=get_pending_count()
    )


@pipeline_kpis_bp.route('/api/pipeline_kpis')
@role_required('admin', 'dev', 'qa')
def pipeline_kpis_api():
    data = get_pipeline_kpis()
    
    # Optional: filter by branch
    branch_filter = request.args.get('branch')
    if branch_filter and 'builds' in data:
        data['builds'] = [b for b in data['builds'] if b.get('branch') == branch_filter]
    
    return jsonify(data)


@pipeline_kpis_bp.route('/api/running_stages')
@role_required('admin', 'dev', 'qa')
def running_stages():
    return jsonify(get_running_stages())


@pipeline_kpis_bp.route('/api/pipeline_details')
@role_required('admin', 'dev', 'qa')
def pipeline_details_api():
    return jsonify(get_pipeline_details_summary())


@pipeline_kpis_bp.route('/api/build', methods=['POST'])
@role_required('admin')
def build():
    success, message = trigger_build()
    if success:
        return jsonify({'queued': True, 'message': message})
    return jsonify({'queued': False, 'error': message}), 500


@pipeline_kpis_bp.route('/api/abort/<int:build_number>', methods=['POST'])
@role_required('admin')
def abort(build_number):
    success, message = abort_build(build_number)
    if success:
        return jsonify({'aborted': True, 'message': message})
    return jsonify({'aborted': False, 'error': message}), 500

@pipeline_kpis_bp.route('/api/vm-metrics')
@role_required('admin')
def vm_metrics_api():
    from flask import jsonify
    return jsonify(get_vm_metrics())


# ==================== DATABASE KPI ENDPOINTS ====================
# These endpoints calculate KPIs from persistent PostgreSQL data

@pipeline_kpis_bp.route('/api/pipeline_kpis_db')
@role_required('admin', 'dev', 'qa')
def pipeline_kpis_db():
    """
    Get pipeline KPIs calculated from database.
    More stable and historical than API-only approach.
    Data is persisted and includes all past builds.
    """
    data = get_kpis_from_database()
    return jsonify(data)


@pipeline_kpis_bp.route('/api/recent_builds_db')
@role_required('admin', 'dev', 'qa')
def recent_builds_db():
    """
    Get recent builds from database with their stages.
    """
    limit = request.args.get('limit', default=20, type=int)
    limit = min(limit, 100)  # Max 100
    
    builds = get_recent_builds_from_database(limit=limit)
    
    return jsonify({
        'source': 'database',
        'builds': builds,
        'count': len(builds),
    })


@pipeline_kpis_bp.route('/api/branch_statistics')
@role_required('admin', 'dev', 'qa')
def branch_statistics():
    """
    Get detailed statistics per branch from database.
    Useful for multibranch pipelines.
    """
    stats = get_build_statistics_by_branch()
    
    return jsonify({
        'source': 'database',
        'branches': stats,
        'count': len(stats),
    })