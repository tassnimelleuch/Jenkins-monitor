from flask import Response, session, jsonify, render_template, stream_with_context
from deployment_kpis import deployment_kpis_bp
from services.access_service import admin_required, build_manager_required, dashboard_user_required
from services.background_refresh_service import (
    get_cached_cluster_metrics_payload,
    get_cached_deployment_kpis_payload,
)
from services.live_stream_service import iter_deployment_live_events


def _filter_deployment_payload_for_role(payload, role):
    if role != 'tester':
        return payload

    source = payload if isinstance(payload, dict) else {}
    data = source.get('data') if isinstance(source.get('data'), dict) else {}
    return {
        'connected': bool(source.get('connected')),
        'message': source.get('message'),
        'data': {
            'latest_image': data.get('latest_image') or {},
        },
    }


@deployment_kpis_bp.route('/deployment_kpis')
@dashboard_user_required
def deployment_kpis():
    return render_template(
        'deployment_kpis.html',
        username=session.get('username'),
        role=session.get('role')
    )


@deployment_kpis_bp.route('/deployment_kpis/api/cluster')
@dashboard_user_required
def deployment_kpis_cluster():
    result = get_cached_deployment_kpis_payload()
    status_code = 200 if result.get('connected') else 503
    return jsonify(_filter_deployment_payload_for_role(result, session.get('role'))), status_code


@deployment_kpis_bp.route('/api/cluster-metrics')
@admin_required
def cluster_metrics_api():
    return jsonify(get_cached_cluster_metrics_payload())


@deployment_kpis_bp.route('/api/deployment/stream')
@build_manager_required
def deployment_live_stream():
    response = Response(
        stream_with_context(
            iter_deployment_live_events(include_cluster_metrics=session.get('role') == 'admin')
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response



@deployment_kpis_bp.route('/api/debug-metrics')
@admin_required
def debug_metrics():
    from flask import jsonify
    from collectors.prometheus_collector import query_range_series, query
    from services.metrics_service import _now_range

    start, end = _now_range(30)

    raw_labels = query(
        'count by (namespace, container, pod) '
        '(container_cpu_usage_seconds_total{container!="POD",container!=""})'
    )

    simple_ns = query_range_series(
        'sum by (namespace) (rate(container_cpu_usage_seconds_total'
        '{container!="POD",container!=""}[5m]))',
        start, end, step="120s", label="namespace"
    )

    pod_info_sample = query(
        'count by (namespace) (kube_pod_info)'
    )

    return jsonify({
        "simple_namespace_series_keys": list(simple_ns.keys()),
        "simple_namespace_series_empty": len(simple_ns) == 0,
        "raw_labels_scalar": raw_labels,
        "pod_info_scalar": pod_info_sample,
    })
