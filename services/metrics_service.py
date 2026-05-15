import logging
from collectors.prometheus_collector import query, query_range, query_range_series
from services.prometheus_queries import (
    CLUSTER_NODE_COUNT_QUERY,
    CLUSTER_NODE_CPU_PCT_QUERY,
    CLUSTER_NODE_RAM_PCT_QUERY,
    CLUSTER_POD_COUNT_QUERY,
    CLUSTER_POD_CPU_PCT_QUERY,
    CLUSTER_POD_RAM_LIMIT_BYTES_QUERY,
    CLUSTER_POD_RAM_USED_BYTES_QUERY,
    DEFAULT_HISTORY_STEP,
    NAMESPACE_CPU_HISTORY_QUERIES,
    NAMESPACE_DISK_HISTORY_QUERIES,
    NAMESPACE_NET_HISTORY_QUERIES,
    NAMESPACE_RAM_HISTORY_QUERIES,
    VM_CPU_PCT_QUERY,
    VM_CPU_PER_CORE_PCT_QUERY,
    VM_DISK_USED_PCT_QUERY,
    VM_NET_RX_MBPS_QUERY,
    VM_NET_TX_MBPS_QUERY,
    VM_RAM_PCT_HISTORY_QUERY,
    VM_RAM_PCT_QUERY,
    VM_RAM_TOTAL_BYTES_QUERY,
    VM_RAM_USED_BYTES_QUERY,
    now_range_iso,
)

logger = logging.getLogger(__name__)


def _now_range(minutes=30):
    return now_range_iso(minutes)


# ── VM metrics (Jenkins Azure VM, scraped via node_exporter) ─────────────────
def get_vm_metrics():
    """CPU and RAM for the Azure VM running Jenkins."""
    try:
        cpu = query(VM_CPU_PCT_QUERY)
        ram_used = query(VM_RAM_PCT_QUERY)
        ram_total_bytes = query(VM_RAM_TOTAL_BYTES_QUERY)
        ram_used_bytes = query(VM_RAM_USED_BYTES_QUERY)
        disk_used_pct = query(VM_DISK_USED_PCT_QUERY)

        start, end = now_range_iso(30)

        cpu_history = query_range(VM_CPU_PCT_QUERY, start, end, step=DEFAULT_HISTORY_STEP)
        cpu_core_history = query_range_series(
            VM_CPU_PER_CORE_PCT_QUERY,
            start,
            end,
            step=DEFAULT_HISTORY_STEP,
            label="cpu",
        )
        ram_history = query_range(
            VM_RAM_PCT_HISTORY_QUERY, start, end, step=DEFAULT_HISTORY_STEP
        )
        net_rx_history = query_range(
            VM_NET_RX_MBPS_QUERY, start, end, step=DEFAULT_HISTORY_STEP
        )
        net_tx_history = query_range(
            VM_NET_TX_MBPS_QUERY, start, end, step=DEFAULT_HISTORY_STEP
        )
        disk_used_pct_history = query_range(
            VM_DISK_USED_PCT_QUERY, start, end, step=DEFAULT_HISTORY_STEP
        )

        return {
            "connected": True,
            "cpu_pct": round(cpu, 1) if cpu is not None else None,
            "ram_pct": round(ram_used, 1) if ram_used is not None else None,
            "ram_used_gb": round(ram_used_bytes / 1e9, 2) if ram_used_bytes else None,
            "ram_total_gb": round(ram_total_bytes / 1e9, 2) if ram_total_bytes else None,
            "disk_pct": round(disk_used_pct, 1) if disk_used_pct is not None else None,
            "cpu_history": cpu_history,
            "cpu_core_history": cpu_core_history,
            "ram_history": ram_history,
            "net_rx_history": net_rx_history,
            "net_tx_history": net_tx_history,
            "disk_used_pct_history": disk_used_pct_history,
        }
    except Exception as e:
        logger.error("get_vm_metrics error: %s", e)
        return {"connected": False, "message": str(e)}


# ── AKS cluster metrics (kube-state-metrics + cAdvisor) ──────────────────────
def get_cluster_metrics():
    """CPU and RAM aggregated across all AKS nodes and pods."""
    try:
        # ── Scalar gauges ────────────────────────────────────────────────────
        node_cpu = query(CLUSTER_NODE_CPU_PCT_QUERY)
        node_ram = query(CLUSTER_NODE_RAM_PCT_QUERY)
        pod_cpu = query(CLUSTER_POD_CPU_PCT_QUERY)
        pod_ram_used = query(CLUSTER_POD_RAM_USED_BYTES_QUERY)
        pod_ram_limit = query(CLUSTER_POD_RAM_LIMIT_BYTES_QUERY)
        pod_count = query(CLUSTER_POD_COUNT_QUERY)
        node_count = query(CLUSTER_NODE_COUNT_QUERY)

        start, end = now_range_iso(30)

        # ── Helper ───────────────────────────────────────────────────────────
        def _first_series(label, queries):
            for i, (q, lbl) in enumerate(queries):
                try:
                    data = query_range_series(q, start, end, step=DEFAULT_HISTORY_STEP, label=lbl)
                    if data:
                        logger.info(
                            "_first_series[%s] matched query #%d → %d series | %.120s",
                            label, i, len(data), q
                        )
                        return data
                    logger.debug(
                        "_first_series[%s] query #%d empty | %.80s", label, i, q
                    )
                except Exception as exc:
                    logger.warning(
                        "_first_series[%s] query #%d error: %s | %.80s", label, i, exc, q
                    )
            logger.warning(
                "_first_series[%s] ALL %d queries returned empty", label, len(queries)
            )
            return {}

        # ── Node-level history ───────────────────────────────────────────────
        node_cpu_history = query_range(
            CLUSTER_NODE_CPU_PCT_QUERY, start, end, step=DEFAULT_HISTORY_STEP
        )
        node_ram_history = query_range(
            CLUSTER_NODE_RAM_PCT_QUERY, start, end, step=DEFAULT_HISTORY_STEP
        )

        ns_cpu_history = _first_series("cpu", NAMESPACE_CPU_HISTORY_QUERIES)
        ns_ram_history = _first_series("ram", NAMESPACE_RAM_HISTORY_QUERIES)
        ns_net_history = _first_series("net", NAMESPACE_NET_HISTORY_QUERIES)
        ns_disk_history = _first_series("disk", NAMESPACE_DISK_HISTORY_QUERIES)

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
            "namespace_net_history": ns_net_history,
            "namespace_disk_history": ns_disk_history,
        }
    except Exception as e:
        logger.error("get_cluster_metrics error: %s", e)
        return {"connected": False, "message": str(e)}
