from flask import session, jsonify, render_template
from deployment_kpis import deployment_kpis_bp
from models import get_pending_count
from services.access_service import role_required
from services.deployment_kpis_service import get_deployment_kpis
from services.metrics_service import get_cluster_metrics

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


@deployment_kpis_bp.route('/api/cluster-metrics')
def cluster_metrics_api():
    from flask import jsonify
    return jsonify(get_cluster_metrics())



@deployment_kpis_bp.route('/api/debug-metrics')
def debug_metrics():
    from flask import jsonify
    from collectors.prometheus_collector import query_range_series, query
    from services.metrics_service import _now_range

    start, end = _now_range(30)

    # What labels do your container metrics actually have?
    raw_labels = query(
        'count by (namespace, container, pod) '
        '(container_cpu_usage_seconds_total{container!="POD",container!=""})'
    )

    # Try the simplest possible series query
    simple_ns = query_range_series(
        'sum by (namespace) (rate(container_cpu_usage_seconds_total'
        '{container!="POD",container!=""}[5m]))',
        start, end, step="120s", label="namespace"
    )

    # Check what kube_pod_info looks like
    pod_info_sample = query(
        'count by (namespace) (kube_pod_info)'
    )

    return jsonify({
        "simple_namespace_series_keys": list(simple_ns.keys()),
        "simple_namespace_series_empty": len(simple_ns) == 0,
        "raw_labels_scalar": raw_labels,
        "pod_info_scalar": pod_info_sample,
    })
