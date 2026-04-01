import os
from kubernetes import client, config


def _load_kube_config():
    cfg_path = os.path.expanduser(
        os.getenv("KUBECONFIG", os.getenv("KUBE_CONFIG_PATH", "~/.kube/aks-config"))
    )
    config.load_kube_config(config_file=cfg_path)
    return cfg_path


def _count_by_namespace(items):
    counts = {}
    for item in items:
        ns = getattr(item.metadata, "namespace", "default") or "default"
        counts[ns] = counts.get(ns, 0) + 1
    return counts


def _count_pods_by_phase(pods):
    phases = {}
    for pod in pods:
        phase = getattr(pod.status, "phase", "Unknown") or "Unknown"
        phases[phase] = phases.get(phase, 0) + 1
    return phases


def get_cluster_snapshot():
    cfg = _load_kube_config()
    v1 = client.CoreV1Api()
    apps = client.AppsV1Api()

    pods = v1.list_pod_for_all_namespaces(watch=False).items
    replica_sets = apps.list_replica_set_for_all_namespaces(watch=False).items
    pvcs = v1.list_persistent_volume_claim_for_all_namespaces(watch=False).items

    kube_system_pods = v1.list_namespaced_pod(namespace="kube-system", watch=False).items

    return {
        "config_source": cfg,
        "pods_total": len(pods),
        "replica_sets_total": len(replica_sets),
        "pvcs_total": len(pvcs),
        "pods_by_phase": _count_pods_by_phase(pods),
        "pods_by_namespace": _count_by_namespace(pods),
        "replica_sets_by_namespace": _count_by_namespace(replica_sets),
        "pvcs_by_namespace": _count_by_namespace(pvcs),
        "kube_system_pods_total": len(kube_system_pods),
        "kube_system_pod_names": sorted(p.metadata.name for p in kube_system_pods),
    }