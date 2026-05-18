from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple


@dataclass
class DailyCostRow:
    day: str
    total: float


@dataclass
class ResourceGroupCost:
    name: str
    total: float
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
    def _empty_days(year: int, month: int) -> Dict[str, float]:
        _, last_day = monthrange(year, month)
        return {
            date(year, month, day).isoformat(): 0.0
            for day in range(1, last_day + 1)
        }

    @staticmethod
    def _previous_month(year: int, month: int) -> Tuple[int, int]:
        if month == 1:
            return year - 1, 12
        return year, month - 1

    # ------------------------------------------------------------------
    # Payload builders
    # ------------------------------------------------------------------

    def _build_query_payload(self, year: int, month: int) -> dict:
        start, end = self._month_bounds(year, month)
        return {
            "type": "ActualCost",
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
                "grouping": [],  # no grouping needed, just daily total
            },
        }

    def _build_rg_payload(self, year: int, month: int) -> dict:
        start, end = self._month_bounds(year, month)
        return {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": start.isoformat() + "Z",
                "to": end.isoformat() + "Z",
            },
            "dataset": {
                "granularity": "None",
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

    # ------------------------------------------------------------------
    # Row parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _rows_to_daily_map(result: dict) -> Tuple[Dict[str, float], dict]:
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

        meta = {
            "columns": list(col_index.keys()),
            "row_count": len(rows or []),
            "date_col": date_col,
            "cost_col": cost_col,
        }

        if cost_col is None or date_col is None:
            return {}, meta

        out: Dict[str, float] = {}
        for row in rows:
            day = FinOpsService._date_int_to_iso(row[col_index[date_col]])
            cost = float(row[col_index[cost_col]])
            out[day] = out.get(day, 0.0) + cost

        return out, meta

    def _parse_rg_rows(self, result: dict) -> List[ResourceGroupCost]:
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

        rg_map: Dict[str, Dict] = {}
        for row in rows:
            rg = str(row[col_index[rg_col]] or "Unknown").lower()
            rtype = str(row[col_index[type_col]] or "").lower() if type_col else ""
            cost = float(row[col_index[cost_col]])

            if rg not in rg_map:
                rg_map[rg] = {"total": 0.0, "by_type": {}}

            rg_map[rg]["total"] += cost
            rg_map[rg]["by_type"][rtype] = rg_map[rg]["by_type"].get(rtype, 0.0) + cost

        result_list = [
            ResourceGroupCost(
                name=rg_name,
                total=round(data["total"], 4),
                by_resource_type={k: round(v, 4) for k, v in data["by_type"].items()},
            )
            for rg_name, data in rg_map.items()
        ]
        result_list.sort(key=lambda x: x.total, reverse=True)
        return result_list

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_previous_week_change_from_totals(totals: List[float]) -> Optional[float]:
        if len(totals) < 14:
            return None
        current = sum(totals[-7:])
        previous = sum(totals[-14:-7])
        if previous == 0:
            return None
        return ((current - previous) / previous) * 100

    @staticmethod
    def _compute_change(current: float, previous: float) -> Dict[str, Optional[float]]:
        previous = float(previous or 0.0)
        current = float(current or 0.0)
        amount_change = round(current - previous, 2)
        pct_change = (
            None if previous == 0
            else round(((current - previous) / previous) * 100, 2)
        )
        return {"amount": amount_change, "pct": pct_change}

    # ------------------------------------------------------------------
    # Core data loader
    # ------------------------------------------------------------------

    def _load_daily_rows(
        self,
        year: int,
        month: int,
    ) -> Tuple[List[DailyCostRow], dict, List[float]]:
        days = self._empty_days(year, month)

        result = self.provider.query_usage(self._build_query_payload(year, month))
        daily, meta = self._rows_to_daily_map(result)

        for day_key, cost in daily.items():
            if day_key in days:
                days[day_key] += cost

        rows = [
            DailyCostRow(day=day_key, total=round(days[day_key], 4))
            for day_key in sorted(days.keys())
        ]
        totals = [row.total for row in rows]
        return rows, meta, totals

    def _build_summary(self, rows: List[DailyCostRow]) -> Dict[str, Optional[float]]:
        total_cost = sum(row.total for row in rows)
        avg_daily_cost = total_cost / len(rows) if rows else 0.0
        highest_day = max(rows, key=lambda r: r.total) if rows else None
        return {
            "total_cost": round(total_cost, 2),
            "average_daily_cost": round(avg_daily_cost, 2),
            "highest_day": highest_day.day if highest_day else None,
            "highest_day_cost": round(highest_day.total, 2) if highest_day else 0.0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_daily_cost_storage_snapshot(self, year: int, month: int) -> dict:
        year, month = int(year), int(month)
        rows, meta, _ = self._load_daily_rows(year, month)
        return {
            "year": year,
            "month": month,
            "currency_code": "USD",
            "meta": meta,
            "rows": [{"day": row.day, "total_cost": row.total} for row in rows],
        }

    def get_resource_group_cost_storage_snapshot(self, year: int, month: int) -> dict:
        year, month = int(year), int(month)
        payload = self._build_rg_payload(year, month)
        result = self.provider.query_usage(payload)
        rg_costs = self._parse_rg_rows(result)
        return {
            "year": year,
            "month": month,
            "currency_code": "USD",
            "total_cost": round(sum(rg.total for rg in rg_costs), 4),
            "resource_groups": [
                {
                    "name": rg.name,
                    "total": round(rg.total, 4),
                    "by_resource_type": rg.by_resource_type,
                }
                for rg in rg_costs
            ],
        }

    def get_daily_cost_chart(self, year: int, month: int) -> dict:
        year, month = int(year), int(month)
        rows, meta, totals = self._load_daily_rows(year, month)
        current_summary = self._build_summary(rows)

        prev_year, prev_month = self._previous_month(year, month)
        prev_rows, _, prev_totals = self._load_daily_rows(prev_year, prev_month)
        previous_summary = self._build_summary(prev_rows)

        # Pad with previous month tail if current month is short
        all_totals = (prev_totals[-7:] + totals) if len(totals) < 14 else totals
        previous_week_change = self._compute_previous_week_change_from_totals(all_totals)

        return {
            "year": year,
            "month": month,
            "labels": [row.day for row in rows],
            "series": {
                "total": [row.total for row in rows],
                "previous_month_total": [row.total for row in prev_rows],
            },
            "meta": meta,
            "summary": {
                **current_summary,
                "previous_week_change_pct": (
                    round(previous_week_change, 2)
                    if previous_week_change is not None else None
                ),
                "previous_month_label": f"{prev_year}-{prev_month:02d}",
                "previous_month": previous_summary,
                "delta": {
                    "total_cost": self._compute_change(
                        current_summary["total_cost"], previous_summary["total_cost"]
                    ),
                    "average_daily_cost": self._compute_change(
                        current_summary["average_daily_cost"], previous_summary["average_daily_cost"]
                    ),
                    "highest_day_cost": self._compute_change(
                        current_summary["highest_day_cost"], previous_summary["highest_day_cost"]
                    ),
                },
            },
        }

    def get_resource_group_costs(self, year: int, month: int) -> dict:
        year, month = int(year), int(month)
        snapshot = self.get_resource_group_cost_storage_snapshot(year, month)
        return {
            "year": snapshot["year"],
            "month": snapshot["month"],
            "total_cost": round(snapshot["total_cost"], 2),
            "resource_groups": [
                {
                    "name": item["name"],
                    "total": round(item["total"], 2),
                    "by_resource_type": item["by_resource_type"],
                }
                for item in snapshot["resource_groups"]
            ],
        }