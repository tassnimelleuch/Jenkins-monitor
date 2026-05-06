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

    @staticmethod
    def _previous_month(year: int, month: int) -> Tuple[int, int]:
        if month == 1:
            return year - 1, 12
        return year, month - 1

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

    @staticmethod
    def _compute_change(current: float, previous: float) -> Dict[str, Optional[float]]:
        previous = float(previous or 0.0)
        current = float(current or 0.0)
        amount_change = round(current - previous, 2)
        if previous == 0:
            pct_change = None
        else:
            pct_change = round(((current - previous) / previous) * 100, 2)
        return {
            "amount": amount_change,
            "pct": pct_change,
        }

    def _load_daily_rows(
        self,
        year: int,
        month: int,
        mode: str = "actual",
        only: str = "all",
    ) -> Tuple[List[DailyCostRow], dict, List[float]]:
        days = self._empty_days(year, month)

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

        for day_key, buckets in daily.items():
            if day_key in days:
                days[day_key]["aks"] += buckets.get("aks", 0.0)
                days[day_key]["vm"] += buckets.get("vm", 0.0)
                days[day_key]["other"] += buckets.get("other", 0.0)

        rows: List[DailyCostRow] = []
        subscription_totals: List[float] = []

        for day_key in sorted(days.keys()):
            aks_val = round(days[day_key]["aks"], 4)
            vm_val = round(days[day_key]["vm"], 4)
            other_val = round(days[day_key]["other"], 4)
            subscription_total = round(aks_val + vm_val + other_val, 4)
            subscription_totals.append(subscription_total)

            if only == "aks":
                aks, vm, total = aks_val, 0.0, aks_val
            elif only == "vm":
                aks, vm, total = 0.0, vm_val, vm_val
            elif only == "subscription":
                aks, vm, total = aks_val, vm_val, subscription_total
            else:
                aks, vm, total = aks_val, vm_val, round(aks_val + vm_val + other_val, 4)

            rows.append(DailyCostRow(day=day_key, aks=aks, vm=vm, total=total))

        return rows, meta, subscription_totals

    def _build_summary(self, rows: List[DailyCostRow]) -> Dict[str, Optional[float]]:
        total_cost = sum(row.total for row in rows)
        aks_total = sum(row.aks for row in rows)
        vm_total = sum(row.vm for row in rows)
        avg_daily_cost = total_cost / len(rows) if rows else 0.0
        highest_day = max(rows, key=lambda row: row.total) if rows else None

        return {
            "total_cost": round(total_cost, 2),
            "aks_total": round(aks_total, 2),
            "vm_total": round(vm_total, 2),
            "average_daily_cost": round(avg_daily_cost, 2),
            "highest_day": highest_day.day if highest_day else None,
            "highest_day_cost": round(highest_day.total, 2) if highest_day else 0.0,
        }
       
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
        rows, meta, subscription_totals = self._load_daily_rows(year, month, mode, only)
        current_summary = self._build_summary(rows)
        prev_year, prev_month = self._previous_month(year, month)
        prev_rows, _, prev_subscription_totals = self._load_daily_rows(prev_year, prev_month, mode, only)
        previous_summary = self._build_summary(prev_rows)

        totals = [row.total for row in rows]
        if len(totals) < 14:
            previous_totals_for_week = [row.total for row in prev_rows]
            totals = previous_totals_for_week[-7:] + totals

        previous_week_change = self._compute_previous_week_change_from_totals(totals)
        previous_month_label = f"{prev_year}-{prev_month:02d}"

        return {
            "year": year,
            "month": month,
            "mode": mode,
            "only": only,
            "labels": [row.day for row in rows],
            "series": {
                "aks": [row.aks for row in rows],
                "vm": [row.vm for row in rows],
                "subscription_total": subscription_totals if only == "subscription" else [],
                "previous_month_subscription_total": prev_subscription_totals if only == "subscription" else [],
            },
            "meta": meta,
            "summary": {
                **current_summary,
                "previous_week_change_pct": round(previous_week_change, 2) if previous_week_change is not None else None,
                "previous_month_label": previous_month_label,
                "previous_month": previous_summary,
                "delta": {
                    "total_cost": self._compute_change(current_summary["total_cost"], previous_summary["total_cost"]),
                    "aks_total": self._compute_change(current_summary["aks_total"], previous_summary["aks_total"]),
                    "vm_total": self._compute_change(current_summary["vm_total"], previous_summary["vm_total"]),
                    "average_daily_cost": self._compute_change(current_summary["average_daily_cost"], previous_summary["average_daily_cost"]),
                    "highest_day_cost": self._compute_change(current_summary["highest_day_cost"], previous_summary["highest_day_cost"]),
                },
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
