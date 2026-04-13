from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set

import requests
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient


class FinOpsDailyCostService:
    def __init__(
        self,
        subscription_id: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        aks_resource_group: str,
        aks_cluster_name: str,
        vm_resource_group: str,
        vm_name: str,
    ) -> None:
        missing = [
            name
            for name, value in (
                ("subscription_id", subscription_id),
                ("tenant_id", tenant_id),
                ("client_id", client_id),
                ("client_secret", client_secret),
                ("aks_resource_group", aks_resource_group),
                ("aks_cluster_name", aks_cluster_name),
                ("vm_resource_group", vm_resource_group),
                ("vm_name", vm_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        self.subscription_id = subscription_id
        self.aks_resource_group = aks_resource_group
        self.aks_cluster_name = aks_cluster_name
        self.vm_resource_group = vm_resource_group
        self.vm_name = vm_name

        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        self.credential = credential
        self.resource_client = ResourceManagementClient(credential, subscription_id)
        self.aks_client = ContainerServiceClient(credential, subscription_id)
        self.compute_client = ComputeManagementClient(credential, subscription_id)
        self.network_client = NetworkManagementClient(credential, subscription_id)

    @staticmethod
    def _parse_resource_id(resource_id: str) -> Dict[str, str]:
        parts = [p for p in resource_id.strip("/").split("/") if p]
        result = {}
        for i in range(0, len(parts) - 1, 2):
            result[parts[i].lower()] = parts[i + 1]
        return result

    @staticmethod
    def _normalize_resource_id(resource_id: str) -> str:
        return resource_id.strip().lower()

    def _get_vm_resource_ids(self) -> Set[str]:
        ids: Set[str] = set()

        vm = self.compute_client.virtual_machines.get(self.vm_resource_group, self.vm_name)
        if vm.id:
            ids.add(self._normalize_resource_id(vm.id))

        if (
            vm.storage_profile
            and vm.storage_profile.os_disk
            and vm.storage_profile.os_disk.managed_disk
            and vm.storage_profile.os_disk.managed_disk.id
        ):
            ids.add(self._normalize_resource_id(vm.storage_profile.os_disk.managed_disk.id))

        if vm.storage_profile and vm.storage_profile.data_disks:
            for disk in vm.storage_profile.data_disks:
                if disk.managed_disk and disk.managed_disk.id:
                    ids.add(self._normalize_resource_id(disk.managed_disk.id))

        if vm.network_profile and vm.network_profile.network_interfaces:
            for nic_ref in vm.network_profile.network_interfaces:
                if not nic_ref.id:
                    continue

                nic_id = self._normalize_resource_id(nic_ref.id)
                ids.add(nic_id)

                nic_parts = self._parse_resource_id(nic_ref.id)
                nic_rg = nic_parts.get("resourcegroups")
                nic_name = nic_parts.get("networkinterfaces")
                if not nic_rg or not nic_name:
                    continue

                nic = self.network_client.network_interfaces.get(nic_rg, nic_name)
                if nic.ip_configurations:
                    for ip_cfg in nic.ip_configurations:
                        if ip_cfg.public_ip_address and ip_cfg.public_ip_address.id:
                            ids.add(self._normalize_resource_id(ip_cfg.public_ip_address.id))

        return ids

    def _get_aks_resource_ids(self) -> Set[str]:
        ids: Set[str] = set()

        cluster = self.aks_client.managed_clusters.get(self.aks_resource_group, self.aks_cluster_name)
        if cluster.id:
            ids.add(self._normalize_resource_id(cluster.id))

        for res in self.resource_client.resources.list_by_resource_group(self.aks_resource_group):
            if res.id:
                ids.add(self._normalize_resource_id(res.id))

        node_rg = getattr(cluster, "node_resource_group", None)
        if node_rg:
            for res in self.resource_client.resources.list_by_resource_group(node_rg):
                if res.id:
                    ids.add(self._normalize_resource_id(res.id))

        return ids

    def _query_daily_costs(self, start_date: str, end_date: str) -> List[dict]:
        token = self.credential.get_token("https://management.azure.com/.default").token
        scope = f"/subscriptions/{self.subscription_id}"
        url = (
            f"https://management.azure.com{scope}"
            f"/providers/Microsoft.CostManagement/query"
            f"?api-version=2025-03-01"
        )

        body = {
            "type": "Usage",
            "timeframe": "Custom",
            "timePeriod": {
                "from": f"{start_date}T00:00:00Z",
                "to": f"{end_date}T23:59:59Z",
            },
            "dataset": {
                "granularity": "Daily",
                "aggregation": {
                    "totalCost": {
                        "name": "PreTaxCost",
                        "function": "Sum",
                    }
                },
                "grouping": [
                    {
                        "type": "Dimension",
                        "name": "ResourceId",
                    }
                ],
            },
        }

        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Azure Cost Management error: {response.status_code} {response.text}"
            )

        data = response.json()
        props = data.get("properties", {})
        columns = props.get("columns", [])
        rows = props.get("rows", [])

        column_index = {col["name"]: i for i, col in enumerate(columns)}

        required = ["UsageDate", "ResourceId", "PreTaxCost"]
        missing = [c for c in required if c not in column_index]
        if missing:
            raise RuntimeError(f"Unexpected Azure cost response. Missing columns: {missing}")

        normalized_rows = []
        for row in rows:
            usage_date_num = row[column_index["UsageDate"]]
            resource_id = row[column_index["ResourceId"]]
            cost = row[column_index["PreTaxCost"]]

            if not resource_id:
                continue

            usage_date = datetime.strptime(str(usage_date_num), "%Y%m%d").strftime("%Y-%m-%d")
            normalized_rows.append(
                {
                    "date": usage_date,
                    "resourceId": self._normalize_resource_id(str(resource_id)),
                    "cost": float(cost),
                }
            )

        return normalized_rows

    def get_daily_bars(self, start_date: str, end_date: str) -> dict:
        vm_ids = self._get_vm_resource_ids()
        aks_ids = self._get_aks_resource_ids()
        rows = self._query_daily_costs(start_date, end_date)

        per_day = defaultdict(lambda: {"vm": 0.0, "aks": 0.0})

        for row in rows:
            day = row["date"]
            rid = row["resourceId"]
            cost = row["cost"]

            if rid in vm_ids:
                per_day[day]["vm"] += cost
            elif rid in aks_ids:
                per_day[day]["aks"] += cost

        result = []
        for day in sorted(per_day.keys()):
            result.append(
                {
                    "date": day,
                    "vm": round(per_day[day]["vm"], 4),
                    "aks": round(per_day[day]["aks"], 4),
                }
            )

        return {
            "subscriptionId": self.subscription_id,
            "vmResourceGroup": self.vm_resource_group,
            "vmName": self.vm_name,
            "aksResourceGroup": self.aks_resource_group,
            "aksClusterName": self.aks_cluster_name,
            "from": start_date,
            "to": end_date,
            "data": result,
            "meta": {
                "vmResourceCount": len(vm_ids),
                "aksResourceCount": len(aks_ids),
            },
        }

    def get_debug_resources(self) -> dict:
        return {
            "vm": sorted(self._get_vm_resource_ids()),
            "aks": sorted(self._get_aks_resource_ids()),
        }
