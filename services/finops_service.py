from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple


AKS_TYPES = {
    "microsoft.containerservice/managedclusters",
    "microsoft.compute/virtualmachinescalesets",
}

VM_TYPES = {
    "microsoft.compute/virtualmachines",
    "microsoft.compute/disks",
    "microsoft.network/networkinterfaces",
    "microsoft.network/publicipaddresses",
        "microsoft.network/loadbalancers",        
}


@dataclass
class DailyCostRow:
    day: str
    aks: float
    vm: float
    total: float


@dataclass
class ResourceGroupCost:
    name: str
    total: float
    aks: float
    vm: float
    other: float
    by_resource_type: Dict[str, float] = field(default_factory=dict)


class FinOpsService:
    def __init__(self, provider):
        self.provider = provider

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _month_bounds(year: int, month: int) -> Tuple[datetime, datetime]:
        last_day = monthrange(year, month)[1]
        start = datetime(year, month, 1, 0, 0, 0)
        end = datetime(year, month, last_day, 23, 59, 59)
        return start, end

    @staticmethod
    def _date_int_to_iso(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            s = value.strip()
            if len(s) >= 10 and s[4] == "-" and s[7] == "-":
                return s[:10]
            s = s.replace("-", "")
        else:
            s = str(int(value))
        if len(s) < 8:
            return ""
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"

    @staticmethod
    def _classify(rtype: str) -> str:
        r = rtype.lower()
        if r in AKS_TYPES or "containerservice/managedclusters" in r or "virtualmachinescalesets" in r:
            return "aks"
        if r in VM_TYPES or "virtualmachines" in r or "disks" in r or "networkinterfaces" in r or "publicipaddresses" in r:
            return "vm"
        return "other"

    @staticmethod
    def _empty_days(year: int, month: int) -> Dict[str, Dict[str, float]]:
        _, last_day = monthrange(year, month)
        data = {}
        for day in range(1, last_day + 1):
            d = date(year, month, day).isoformat()
            data[d] = {"aks": 0.0, "vm": 0.0, "other": 0.0}
        return data

    # ------------------------------------------------------------------
    # Payload builders
    # ------------------------------------------------------------------

    def _build_query_payload(
        self,
        year: int,
        month: int,
        resource_types: Optional[List[str]],
        cost_type: str = "ActualCost",
        group_by: Optional[List[dict]] = None,
    ) -> dict:
        start, end = self._month_bounds(year, month)

        if group_by is None:
            group_by = [{"type": "Dimension", "name": "ResourceType"}]

        payload = {
            "type": cost_type,
            "timeframe": "Custom",
            "timePeriod": {
                "from": start.isoformat() + "Z",
                "to": end.isoformat() + "Z",
            },
            "dataset": {
                "granularity": "Daily",
                "aggregation": {
                    "totalCost": {
                        "name": "PreTaxCost",
                        "function": "Sum",
                    }
                },
                "grouping": group_by,
            },
        }
        if resource_types:
            payload["dataset"]["filter"] = {
                "dimensions": {
                    "name": "ResourceType",
                    "operator": "In",
                    "values": resource_types,
                }
            }
        return payload

    def _build_rg_payload(self, year: int, month: int, cost_type: str = "ActualCost") -> dict:
        """
        Monthly (non-daily) query grouped by ResourceGroup + ResourceType.
        This matches what the Azure portal Cost Analysis shows per resource group.
        """
        start, end = self._month_bounds(year, month)
        return {
            "type": cost_type,
            "timeframe": "Custom",
            "timePeriod": {
                "from": start.isoformat() + "Z",
                "to": end.isoformat() + "Z",
            },
            "dataset": {
                "granularity": "None",          # monthly total, not daily
                "aggregation": {
                    "totalCost": {
                        "name": "PreTaxCost",
                        "function": "Sum",
                    }
                },
                "grouping": [
                    {"type": "Dimension", "name": "ResourceGroupName"},
                    {"type": "Dimension", "name": "ResourceType"},
                ],
            },
        }

    def _build_forecast_payload(
        self,
        year: int,
        month: int,
        resource_types: Optional[List[str]],
    ) -> dict:
        start, end = self._month_bounds(year, month)
        payload = {
            "type": "Usage",
            "timeframe": "Custom",
            "timePeriod": {
                "from": start.isoformat() + "Z",
                "to": end.isoformat() + "Z",
            },
            "includeActualCost": False,
            "includeFreshPartialCost": False,
            "dataset": {
                "granularity": "Daily",
                "aggregation": {
                    "totalCost": {
                        "name": "Cost",
                        "function": "Sum",
                    }
                },
                "grouping": [{"type": "Dimension", "name": "ResourceType"}],
            },
        }
        if resource_types:
            payload["dataset"]["filter"] = {
                "dimensions": {
                    "name": "ResourceType",
                    "operator": "In",
                    "values": resource_types,
                }
            }
        return payload

    # ------------------------------------------------------------------
    # Row parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _rows_to_daily_map(
        result: dict,
        include_other: bool = True,
    ) -> Tuple[Dict[str, Dict[str, float]], dict]:
        props = result.get("properties", {})
        columns = props.get("columns", [])
        rows = props.get("rows", [])

        col_index = {col["name"]: idx for idx, col in enumerate(columns)}

        cost_col = next(
            (c for c in ("PreTaxCost", "Cost", "totalCost") if c in col_index), None
        )
        date_col = next(
            (c for c in ("UsageDate", "Date") if c in col_index), None
        )
        type_col = "ResourceType" if "ResourceType" in col_index else None

        meta = {
            "columns": list(col_index.keys()),
            "row_count": len(rows or []),
            "date_col": date_col,
            "type_col": type_col,
            "cost_col": cost_col,
        }

        if cost_col is None or date_col is None or type_col is None:
            return {}, meta

        out: Dict[str, Dict[str, float]] = {}
        for row in rows:
            day = FinOpsService._date_int_to_iso(row[col_index[date_col]])
            rtype = str(row[col_index[type_col]] or "").lower()
            cost = float(row[col_index[cost_col]])

            if day not in out:
                out[day] = {"aks": 0.0, "vm": 0.0, "other": 0.0}

            bucket = FinOpsService._classify(rtype)
            if bucket != "other" or include_other:
                out[day][bucket] += cost

        return out, meta

    def _parse_rg_rows(self, result: dict) -> List[ResourceGroupCost]:
        """
        Parse the resource-group + resource-type grouped response into
        ResourceGroupCost objects — one per resource group.
        """
        props = result.get("properties", {})
        columns = props.get("columns", [])
        rows = props.get("rows", [])

        col_index = {col["name"]: idx for idx, col in enumerate(columns)}

        cost_col = next(
            (c for c in ("PreTaxCost", "Cost", "totalCost") if c in col_index), None
        )
        rg_col = next(
            (c for c in ("ResourceGroupName", "ResourceGroup") if c in col_index), None
        )
        type_col = "ResourceType" if "ResourceType" in col_index else None

        if cost_col is None or rg_col is None:
            return []

        # Accumulate per RG
        rg_map: Dict[str, Dict] = {}
        for row in rows:
            rg = str(row[col_index[rg_col]] or "Unknown").lower()
            rtype = str(row[col_index[type_col]] or "").lower() if type_col else ""
            cost = float(row[col_index[cost_col]])

            if rg not in rg_map:
                rg_map[rg] = {"aks": 0.0, "vm": 0.0, "other": 0.0, "by_type": {}}

            bucket = self._classify(rtype)
            rg_map[rg][bucket] += cost
            rg_map[rg]["by_type"][rtype] = rg_map[rg]["by_type"].get(rtype, 0.0) + cost

        result_list: List[ResourceGroupCost] = []
        for rg_name, data in rg_map.items():
            total = round(data["aks"] + data["vm"] + data["other"], 4)
            result_list.append(ResourceGroupCost(
                name=rg_name,
                total=total,
                aks=round(data["aks"], 4),
                vm=round(data["vm"], 4),
                other=round(data["other"], 4),
                by_resource_type={k: round(v, 4) for k, v in data["by_type"].items()},
            ))

        # Sort descending by total cost, matching portal default
        result_list.sort(key=lambda x: x.total, reverse=True)
        return result_list

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_previous_week_change_from_totals(totals):
        if len(totals) < 14:
            return None

        current = sum(totals[-7:])
        previous = sum(totals[-14:-7])

        if previous == 0:   # 🔥 FIX HERE
            return None

        return ((current - previous) / previous) * 100
       
    def get_daily_cost_chart(
        self,
        year: int,
        month: int,
        mode: str = "actual",
        only: str = "all",
    ) -> dict:
        year = int(year)
        month = int(month)
        mode = str(mode).strip().lower()
        only = str(only).strip().lower()

        days = self._empty_days(year, month)

        only = str(only).strip().lower()

        if only == "aks":
            resource_types = sorted(list(AKS_TYPES))
        elif only == "vm":
            resource_types = sorted(list(VM_TYPES))
        else:
            resource_types = None

        include_other = only in ("subscription", "all")

        if mode == "forecast":
            result = self.provider.forecast_usage(
                self._build_forecast_payload(year, month, resource_types)
            )
        else:
            result = self.provider.query_usage(
                self._build_query_payload(year, month, resource_types, cost_type="ActualCost")
            )

        daily, meta = self._rows_to_daily_map(result, include_other=include_other)

        if meta.get("row_count", 0) == 0 and resource_types is not None:
            if mode == "forecast":
                result = self.provider.forecast_usage(
                    self._build_forecast_payload(year, month, None)
                )
            else:
                result = self.provider.query_usage(
                    self._build_query_payload(year, month, None, cost_type="ActualCost")
                )
            daily, meta = self._rows_to_daily_map(result, include_other=include_other)

        for d, buckets in daily.items():
            if d in days:
                days[d]["aks"] += buckets.get("aks", 0.0)
                days[d]["vm"] += buckets.get("vm", 0.0)
                days[d]["other"] += buckets.get("other", 0.0)

        rows: List[DailyCostRow] = []
        subscription_totals: List[float] = []

        for d in sorted(days.keys()):
            aks_val = round(days[d]["aks"], 4)
            vm_val = round(days[d]["vm"], 4)
            other_val = round(days[d]["other"], 4)
            subscription_total = round(aks_val + vm_val + other_val, 4)
            subscription_totals.append(subscription_total)

            if only == "aks":
                aks, vm, total = aks_val, 0.0, aks_val
            elif only == "vm":
                aks, vm, total = 0.0, vm_val, vm_val
            elif only == "subscription":
                aks, vm, total = aks_val, vm_val, subscription_total
            else:  # all
                aks, vm, total = aks_val, vm_val, round(aks_val + vm_val + other_val, 4)

            rows.append(DailyCostRow(day=d, aks=aks, vm=vm, total=total))

        total_cost = sum(r.total for r in rows)
        avg_daily_cost = total_cost / len(rows) if rows else 0.0
        highest_day = max(rows, key=lambda r: r.total) if rows else None
        totals = [r.total for r in rows]

        if len(totals) < 14:
            prev_month = month - 1 if month > 1 else 12
            prev_year = year if month > 1 else year - 1

            prev = self.get_daily_cost_chart(prev_year, prev_month, mode, only)
            prev_totals = prev["series"].get("subscription_total", []) or prev["series"].get("aks", [])

            totals = prev_totals[-7:] + totals

        previous_week_change = self._compute_previous_week_change_from_totals(totals)

        return {
            "mode": mode,
            "only": only,
            "labels": [r.day for r in rows],
            "series": {
                "aks": [r.aks for r in rows],
                "vm": [r.vm for r in rows],
                "subscription_total": subscription_totals if only == "subscription" else [],
            },
            "meta": meta,
            "summary": {
                "total_cost": round(total_cost, 2),
                "average_daily_cost": round(avg_daily_cost, 2),
                "highest_day": highest_day.day if highest_day else None,
                "highest_day_cost": round(highest_day.total, 2) if highest_day else 0.0,
                "previous_week_change_pct": round(previous_week_change, 2) if previous_week_change is not None else None,
            },
        }

    def get_resource_group_costs(
        self,
        year: int,
        month: int,
        cost_type: str = "ActualCost",
    ) -> dict:
        """
        Returns cost per resource group, matching the Azure portal
        Cost Analysis view grouped by Resource Group.
        Includes AKS, VM, and ALL other resource types.
        """
        year = int(year)
        month = int(month)
        cost_type = str(cost_type).strip()

        payload = self._build_rg_payload(year, month, cost_type=cost_type)
        result = self.provider.query_usage(payload)
        rg_costs = self._parse_rg_rows(result)

        total = round(sum(rg.total for rg in rg_costs), 2)

        return {
            "year": year,
            "month": month,
            "cost_type": cost_type,
            "total_cost": total,
            "resource_groups": [
                {
                    "name": rg.name,
                    "total": round(rg.total, 2),
                    "aks": round(rg.aks, 2),
                    "vm": round(rg.vm, 2),
                    "other": round(rg.other, 2),
                    "by_resource_type": rg.by_resource_type,
                }
                for rg in rg_costs
            ],
        }