# test_aks.py
from kubernetes import client, config

config.load_kube_config(config_file="~/.kube/aks-config")
v1 = client.CoreV1Api()

nodes = v1.list_node()
print(f"✅ Connected! Nodes: {len(nodes.items)}")

pods = v1.list_namespaced_pod(namespace="default")
for p in pods.items:
    print(f"  Pod: {p.metadata.name} → {p.status.phase}")