from __future__ import annotations

from datetime import datetime, timedelta, timezone


VM_JOB = "jenkins-vm"
DEFAULT_HISTORY_STEP = "60s"


def now_range_iso(minutes: int = 30) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    return start.isoformat(), end.isoformat()


# ── VM metrics ───────────────────────────────────────────────────────────────
VM_CPU_PCT_QUERY = (
    '100 - (avg by(instance) (rate(node_cpu_seconds_total'
    f'{{mode="idle", job="{VM_JOB}"}}[5m])) * 100)'
)

VM_RAM_PCT_QUERY = (
    f'(1 - (node_memory_MemAvailable_bytes{{job="{VM_JOB}"}} '
    f'/ node_memory_MemTotal_bytes{{job="{VM_JOB}"}})) * 100'
)

VM_RAM_PCT_HISTORY_QUERY = VM_RAM_PCT_QUERY

VM_RAM_TOTAL_BYTES_QUERY = f'node_memory_MemTotal_bytes{{job="{VM_JOB}"}}'

VM_RAM_USED_BYTES_QUERY = (
    f'node_memory_MemTotal_bytes{{job="{VM_JOB}"}} '
    f'- node_memory_MemAvailable_bytes{{job="{VM_JOB}"}}'
)

VM_VCPUS_QUERY = (
    f'count(count by (cpu) (node_cpu_seconds_total{{mode="idle", job="{VM_JOB}"}}))'
)

VM_NET_RX_MBPS_QUERY = (
    'sum by(instance) (rate(node_network_receive_bytes_total'
    f'{{job="{VM_JOB}",device!~"lo|docker.*|veth.*|br-.*"}}[5m]))'
    ' / 1024 / 1024'
)

VM_NET_TX_MBPS_QUERY = (
    'sum by(instance) (rate(node_network_transmit_bytes_total'
    f'{{job="{VM_JOB}",device!~"lo|docker.*|veth.*|br-.*"}}[5m]))'
    ' / 1024 / 1024'
)

VM_DISK_USED_PCT_QUERY = (
    f'100 - ((node_filesystem_avail_bytes{{job="{VM_JOB}",mountpoint="/"}} '
    f'/ node_filesystem_size_bytes{{job="{VM_JOB}",mountpoint="/"}}) * 100)'
)


# ── AKS cluster metrics ──────────────────────────────────────────────────────
CLUSTER_NODE_CPU_PCT_QUERY = (
    'sum(rate(node_cpu_seconds_total{mode!="idle"}[5m]))'
    ' / scalar(sum(machine_cpu_cores)) * 100'
)

CLUSTER_NODE_RAM_PCT_QUERY = (
    '(1 - sum(node_memory_MemAvailable_bytes)'
    ' / sum(node_memory_MemTotal_bytes)) * 100'
)

CLUSTER_POD_CPU_PCT_QUERY = (
    'sum(rate(container_cpu_usage_seconds_total'
    '{namespace!="",container!="POD",container!=""}[5m]))'
    ' / scalar(sum(machine_cpu_cores)) * 100'
)

CLUSTER_POD_RAM_USED_BYTES_QUERY = (
    'sum(container_memory_working_set_bytes'
    '{namespace!="",container!="POD",container!=""})'
)

CLUSTER_POD_RAM_LIMIT_BYTES_QUERY = (
    'sum(kube_pod_container_resource_limits'
    '{resource="memory", unit="byte"})'
)

CLUSTER_POD_COUNT_QUERY = 'count(kube_pod_info{namespace!="kube-system"})'
CLUSTER_NODE_COUNT_QUERY = 'count(kube_node_info)'
CLUSTER_VCPUS_QUERY = 'sum(machine_cpu_cores)'
CLUSTER_RAM_TOTAL_BYTES_QUERY = 'sum(node_memory_MemTotal_bytes)'


# ── Namespace history fallbacks used by the deployment dashboard ─────────────
NAMESPACE_CPU_HISTORY_QUERIES = [
    (
        'sum by (namespace) (rate(container_cpu_usage_seconds_total'
        '{namespace!="",container!="POD",container!=""}[5m]))'
        ' / scalar(sum(machine_cpu_cores)) * 100',
        "namespace",
    ),
    (
        'sum by (namespace) (rate(container_cpu_usage_seconds_total'
        '{namespace!="",container!="POD",container!=""}[5m]))'
        ' / scalar(sum(kube_node_status_capacity_cpu_cores)) * 100',
        "namespace",
    ),
    (
        'sum by (namespace) (node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate)'
        ' / scalar(sum(machine_cpu_cores)) * 100',
        "namespace",
    ),
    (
        'sum by (namespace) (node_namespace_pod_container:container_cpu_usage_seconds_total:sum_rate)'
        ' / scalar(sum(machine_cpu_cores)) * 100',
        "namespace",
    ),
    (
        'sum by (namespace) (rate(container_cpu_usage_seconds_total'
        '{namespace!="",container!="POD",container!=""}[5m])) * 100',
        "namespace",
    ),
]

NAMESPACE_RAM_HISTORY_QUERIES = [
    (
        'sum by (namespace) (container_memory_working_set_bytes'
        '{namespace!="",container!="POD",container!=""}) / 1e9',
        "namespace",
    ),
    (
        'sum by (kubernetes_namespace) (container_memory_working_set_bytes'
        '{kubernetes_namespace!="",container!="POD",container!=""}) / 1e9',
        "kubernetes_namespace",
    ),
    (
        'sum by (namespace) (container_memory_working_set_bytes'
        '{container!="POD",container!=""}'
        ' * on(pod) group_left(namespace) kube_pod_info) / 1e9',
        "namespace",
    ),
]

NAMESPACE_NET_HISTORY_QUERIES = [
    (
        'sum by (namespace) (rate(container_network_receive_bytes_total'
        '{namespace!="",pod!="",interface!~"lo"}[5m])'
        ' + rate(container_network_transmit_bytes_total'
        '{namespace!="",pod!="",interface!~"lo"}[5m])) / 1024 / 1024',
        "namespace",
    ),
    (
        'sum by (kubernetes_namespace) (rate(container_network_receive_bytes_total'
        '{kubernetes_namespace!="",pod!="",interface!~"lo"}[5m])'
        ' + rate(container_network_transmit_bytes_total'
        '{kubernetes_namespace!="",pod!="",interface!~"lo"}[5m])) / 1024 / 1024',
        "kubernetes_namespace",
    ),
    (
        'sum by (namespace) ((rate(container_network_receive_bytes_total'
        '{pod!=""}[5m]) + rate(container_network_transmit_bytes_total'
        '{pod!=""}[5m])) * on(pod) group_left(namespace) kube_pod_info) / 1024 / 1024',
        "namespace",
    ),
]

NAMESPACE_DISK_HISTORY_QUERIES = [
    (
        'sum by (namespace) (container_fs_usage_bytes'
        '{namespace!="",container!="POD",container!=""}) / 1e9',
        "namespace",
    ),
    (
        'sum by (namespace) (node_namespace_pod_container:container_fs_usage_bytes) / 1e9',
        "namespace",
    ),
    (
        'sum by (kubernetes_namespace) (container_fs_usage_bytes'
        '{kubernetes_namespace!="",container!="POD",container!=""}) / 1e9',
        "kubernetes_namespace",
    ),
    (
        'sum by (namespace) (kubelet_volume_stats_used_bytes{namespace!=""}) / 1e9',
        "namespace",
    ),
]
