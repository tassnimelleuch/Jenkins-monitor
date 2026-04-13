from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Tuple


AKS_TYPES = {
    "microsoft.containerservice/managedclusters",
    "microsoft.compute/virtualmachinescalesets",
}

VM_TYPES = {
    "microsoft.compute/virtualmachines",
    "microsoft.compute/disks",
    "microsoft.network/networkinterfaces",
    "microsoft.network/publicipaddresses",
}


@dataclass
class DailyCostRow:
    day: str
    aks: float
    vm: float
    total: float


class FinOpsService:
    def __init__(self, provider):
        self.provider = provider

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
        # Azure Cost Management can return dates as ints (YYYYMMDD) or ISO strings (YYYY-MM-DD).
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
    def _empty_days(year: int, month: int) -> Dict[str, Dict[str, float]]:
        _, last_day = monthrange(year, month)
        data = {}
        for day in range(1, last_day + 1):
            d = date(year, month, day).isoformat()
            data[d] = {"aks": 0.0, "vm": 0.0}
        return data

    def _build_query_payload(
        self,
        year: int,
        month: int,
        resource_types: List[str] | None,
        cost_type: str = "Usage",
    ) -> dict:
        start, end = self._month_bounds(year, month)

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
                        "function": "Sum"
                    }
                },
                "grouping": [
                    {"type": "Dimension", "name": "ResourceType"}
                ],
            }
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

    def _build_forecast_payload(
        self,
        year: int,
        month: int,
        resource_types: List[str] | None,
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
                        "function": "Sum"
                    }
                },
                "grouping": [
                    {"type": "Dimension", "name": "ResourceType"}
                ],
            }
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

    @staticmethod
    def _rows_to_daily_map(result: dict) -> tuple[Dict[str, Dict[str, float]], dict]:
        props = result.get("properties", {})
        columns = props.get("columns", [])
        rows = props.get("rows", [])

        col_index = {col["name"]: idx for idx, col in enumerate(columns)}

        cost_col = None
        for candidate in ("PreTaxCost", "Cost", "totalCost"):
            if candidate in col_index:
                cost_col = candidate
                break

        date_col = "UsageDate" if "UsageDate" in col_index else ("Date" if "Date" in col_index else None)
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

            bucket = None
            if rtype in AKS_TYPES or "containerservice/managedclusters" in rtype or "virtualmachinescalesets" in rtype:
                bucket = "aks"
            elif rtype in VM_TYPES or "virtualmachines" in rtype or "disks" in rtype or "networkinterfaces" in rtype or "publicipaddresses" in rtype:
                bucket = "vm"

            if bucket is None:
                continue

            if day not in out:
                out[day] = {"aks": 0.0, "vm": 0.0}
            out[day][bucket] += cost
        return out, meta

    @staticmethod
    def _compute_previous_week_change(daily_rows: List[DailyCostRow]) -> float | None:
        if len(daily_rows) < 14:
            return None

        totals = [r.total for r in daily_rows]
        current_week = sum(totals[-7:])
        previous_week = sum(totals[-14:-7])

        if previous_week == 0:
            return None
        return ((current_week - previous_week) / previous_week) * 100.0

    def get_daily_cost_chart(
        self,
        year: int,
        month: int,
        mode: str = "actual",
        only: str = "all",
    ) -> dict:
        days = self._empty_days(year, month)
        resource_types = sorted(list(AKS_TYPES | VM_TYPES))

        if mode == "forecast":
            result = self.provider.forecast_usage(
                self._build_forecast_payload(year, month, resource_types)
            )
        else:
            result = self.provider.query_usage(
                self._build_query_payload(year, month, resource_types, cost_type="ActualCost")
            )

        daily, meta = self._rows_to_daily_map(result)
        if meta.get("row_count", 0) == 0:
            # Retry without ResourceType filter in case the exact values don't match.
            if mode == "forecast":
                result = self.provider.forecast_usage(
                    self._build_forecast_payload(year, month, None)
                )
            else:
                result = self.provider.query_usage(
                    self._build_query_payload(year, month, None, cost_type="ActualCost")
                )
            daily, meta = self._rows_to_daily_map(result)

        for d, buckets in daily.items():
            if d in days:
                days[d]["aks"] += buckets.get("aks", 0.0)
                days[d]["vm"] += buckets.get("vm", 0.0)

        rows: List[DailyCostRow] = []
        for d in sorted(days.keys()):
            aks = days[d]["aks"] if only in ("all", "aks") else 0.0
            vm = days[d]["vm"] if only in ("all", "vm") else 0.0
            rows.append(DailyCostRow(day=d, aks=round(aks, 4), vm=round(vm, 4), total=round(aks + vm, 4)))

        total_cost = sum(r.total for r in rows)
        avg_daily_cost = total_cost / len(rows) if rows else 0.0
        highest_day = max(rows, key=lambda r: r.total) if rows else None
        previous_week_change = self._compute_previous_week_change(rows)

        return {
            "mode": mode,
            "only": only,
            "labels": [r.day for r in rows],
            "series": {
                "aks": [r.aks for r in rows],
                "vm": [r.vm for r in rows],
            },
            "meta": meta,
            "summary": {
                "total_cost": round(total_cost, 2),
                "average_daily_cost": round(avg_daily_cost, 2),
                "highest_day": highest_day.day if highest_day else None,
                "highest_day_cost": round(highest_day.total, 2) if highest_day else 0.0,
                "previous_week_change_pct": round(previous_week_change, 2) if previous_week_change is not None else None,
            }
        }
