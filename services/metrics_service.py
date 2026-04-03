import logging
from providers.prometheus import query, query_range, query_range_series
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _now_range(minutes=30):
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    return start.isoformat(), end.isoformat()


# ── VM metrics (Jenkins Azure VM, scraped via node_exporter) ─────────────────

def get_vm_metrics():
    """CPU and RAM for the Azure VM running Jenkins."""
    try:
        # node_exporter job must be named 'jenkins-vm' in prometheus.yml
        cpu = query(
            '100 - (avg by(instance) (rate(node_cpu_seconds_total'
            '{mode="idle", job="jenkins-vm"}[5m])) * 100)'
        )
        ram_used = query(
            '(1 - (node_memory_MemAvailable_bytes{job="jenkins-vm"} '
            '/ node_memory_MemTotal_bytes{job="jenkins-vm"})) * 100'
        )
        ram_total_bytes = query(
            'node_memory_MemTotal_bytes{job="jenkins-vm"}'
        )
        ram_used_bytes = query(
            'node_memory_MemTotal_bytes{job="jenkins-vm"} '
            '- node_memory_MemAvailable_bytes{job="jenkins-vm"}'
        )
        disk_used_pct = query(
            '100 - ((node_filesystem_avail_bytes{job="jenkins-vm",mountpoint="/"} '
            '/ node_filesystem_size_bytes{job="jenkins-vm",mountpoint="/"}) * 100)'
        )

        start, end = _now_range(30)
        cpu_history = query_range(
            '100 - (avg by(instance) (rate(node_cpu_seconds_total'
            '{mode="idle", job="jenkins-vm"}[5m])) * 100)',
            start, end, step="60s"
        )
        ram_history = query_range(
            '100 - ((node_memory_MemAvailable_bytes{job="jenkins-vm"} '
            '/ node_memory_MemTotal_bytes{job="jenkins-vm"}) * 100)',
            start, end, step="60s"
        )

        return {
            "connected": True,
            "cpu_pct": round(cpu, 1) if cpu is not None else None,
            "ram_pct": round(ram_used, 1) if ram_used is not None else None,
            "ram_used_gb": round(ram_used_bytes / 1e9, 2) if ram_used_bytes else None,
            "ram_total_gb": round(ram_total_bytes / 1e9, 2) if ram_total_bytes else None,
            "disk_pct": round(disk_used_pct, 1) if disk_used_pct else None,
            "cpu_history": cpu_history,
            "ram_history": ram_history,
        }
    except Exception as e:
        logger.error("get_vm_metrics error: %s", e)
        return {"connected": False, "message": str(e)}


# ── AKS cluster metrics (kube-state-metrics + cAdvisor) ──────────────────────

def get_cluster_metrics():
    """CPU and RAM aggregated across all AKS nodes and pods."""
    try:
        node_cpu = query(
            'sum(rate(node_cpu_seconds_total{mode!="idle"}[5m]))'
            ' / (sum(machine_cpu_cores) or sum(kube_node_status_capacity_cpu_cores)) * 100'
        )
        node_ram = query(
            '(1 - sum(node_memory_MemAvailable_bytes)'
            ' / (sum(node_memory_MemTotal_bytes) or sum(kube_node_status_capacity_memory_bytes))) * 100'
        )
        pod_cpu = query(
            'sum(rate(container_cpu_usage_seconds_total'
            '{namespace!="",container!="POD",container!=""}[5m])) * 100'
        )
        pod_ram_used = query(
            'sum(container_memory_working_set_bytes'
            '{namespace!="",container!="POD",container!=""})'
        )
        pod_ram_limit = query(
            'sum(kube_pod_container_resource_limits'
            '{resource="memory", unit="byte"})'
        )
        pod_count = query('count(kube_pod_info{namespace!="kube-system"})')
        node_count = query('count(kube_node_info)')

        start, end = _now_range(30)
        node_cpu_history = query_range(
            'sum(rate(node_cpu_seconds_total{mode!="idle"}[5m]))'
            ' / (sum(machine_cpu_cores) or sum(kube_node_status_capacity_cpu_cores)) * 100',
            start, end, step="60s"
        )
        node_ram_history = query_range(
            '(1 - sum(node_memory_MemAvailable_bytes)'
            ' / (sum(node_memory_MemTotal_bytes) or sum(kube_node_status_capacity_memory_bytes))) * 100',
            start, end, step="60s"
        )
        ns_cpu_history = {}
        ns_ram_history = {}

        ns_cpu_queries = [
            ('sum by (namespace) (rate(container_cpu_usage_seconds_total'
             '{namespace!="",container!="POD",container!=""}[5m]))'
             ' / (sum(machine_cpu_cores) or sum(kube_node_status_capacity_cpu_cores)) * 100', "namespace"),
            ('sum by (kubernetes_namespace) (rate(container_cpu_usage_seconds_total'
             '{kubernetes_namespace!="",container!="POD",container!=""}[5m]))'
             ' / (sum(machine_cpu_cores) or sum(kube_node_status_capacity_cpu_cores)) * 100', "kubernetes_namespace"),
            ('sum by (namespace) (rate(container_cpu_usage_seconds_total'
             '{container!="POD",container!=""}[5m])'
             ' * on(pod) group_left(namespace) kube_pod_info)'
             ' / (sum(machine_cpu_cores) or sum(kube_node_status_capacity_cpu_cores)) * 100', "namespace"),
            ('sum by (namespace) (rate(label_replace(container_cpu_usage_seconds_total'
             '{container!="POD",container!=""}[5m],"pod","$1","pod_name","(.*)") )'
             ' * on(pod) group_left(namespace) kube_pod_info)'
             ' / (sum(machine_cpu_cores) or sum(kube_node_status_capacity_cpu_cores)) * 100', "namespace"),
            ('sum by (namespace) (rate(label_replace(container_cpu_usage_seconds_total'
             '{container!="POD",container!=""}[5m],"pod","$1","kubernetes_pod_name","(.*)") )'
             ' * on(pod) group_left(namespace) kube_pod_info)'
             ' / (sum(machine_cpu_cores) or sum(kube_node_status_capacity_cpu_cores)) * 100', "namespace"),
        ]

        for q, lbl in ns_cpu_queries:
            ns_cpu_history = query_range_series(q, start, end, step="60s", label=lbl)
            if ns_cpu_history:
                break

        ns_ram_queries = [
            ('sum by (namespace) (container_memory_working_set_bytes'
             '{namespace!="",container!="POD",container!=""}) / 1e9', "namespace"),
            ('sum by (kubernetes_namespace) (container_memory_working_set_bytes'
             '{kubernetes_namespace!="",container!="POD",container!=""}) / 1e9', "kubernetes_namespace"),
            ('sum by (namespace) (container_memory_working_set_bytes'
             '{container!="POD",container!=""}'
             ' * on(pod) group_left(namespace) kube_pod_info) / 1e9', "namespace"),
            ('sum by (namespace) (label_replace(container_memory_working_set_bytes'
             '{container!="POD",container!=""},"pod","$1","pod_name","(.*)")'
             ' * on(pod) group_left(namespace) kube_pod_info) / 1e9', "namespace"),
            ('sum by (namespace) (label_replace(container_memory_working_set_bytes'
             '{container!="POD",container!=""},"pod","$1","kubernetes_pod_name","(.*)")'
             ' * on(pod) group_left(namespace) kube_pod_info) / 1e9', "namespace"),
        ]

        for q, lbl in ns_ram_queries:
            ns_ram_history = query_range_series(q, start, end, step="60s", label=lbl)
            if ns_ram_history:
                break

        return {
            "connected": True,
            "node_cpu_pct": round(node_cpu, 1) if node_cpu is not None else None,
            "node_ram_pct": round(node_ram, 1) if node_ram is not None else None,
            "pod_cpu_pct": round(pod_cpu, 1) if pod_cpu is not None else None,
            "pod_ram_used_gb": round(pod_ram_used / 1e9, 2) if pod_ram_used else None,
            "pod_ram_limit_gb": round(pod_ram_limit / 1e9, 2) if pod_ram_limit else None,
            "pod_count": int(pod_count) if pod_count else None,
            "node_count": int(node_count) if node_count else None,
            "node_cpu_history": node_cpu_history,
            "node_ram_history": node_ram_history,
            "namespace_cpu_history": ns_cpu_history,
            "namespace_ram_history": ns_ram_history,
        }
    except Exception as e:
        logger.error("get_cluster_metrics error: %s", e)
        return {"connected": False, "message": str(e)}
