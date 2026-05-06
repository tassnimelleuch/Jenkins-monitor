from __future__ import annotations

from extensions import cache


DAILY_COST_CACHE_VERSION = "v2"
RG_COST_CACHE_VERSION = "v1"


def _daily_cost_payload_is_compatible(payload) -> bool:
    if not isinstance(payload, dict):
        return False

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return False

    required_summary_keys = (
        "aks_total",
        "vm_total",
        "previous_month_label",
        "delta",
    )
    if any(key not in summary for key in required_summary_keys):
        return False

    delta = summary.get("delta")
    if not isinstance(delta, dict):
        return False

    return "total_cost" in delta


def get_cached_daily_cost_chart(
    service,
    year: int,
    month: int,
    mode: str = "actual",
    only: str = "all",
) -> dict:
    year = int(year)
    month = int(month)
    mode = str(mode).strip().lower()
    only = str(only).strip().lower()

    key = f"daily_cost_chart:{DAILY_COST_CACHE_VERSION}:{year}:{month}:{mode}:{only}"
    cached = cache.get(key)
    if cached is not None and _daily_cost_payload_is_compatible(cached):
        return cached

    result = service.get_daily_cost_chart(year, month, mode, only)
    cache.set(key, result, timeout=14400)  # 4 hours
    return result


def get_cached_resource_group_costs(
    service,
    year: int,
    month: int,
    cost_type: str = "ActualCost",
) -> dict:
    year = int(year)
    month = int(month)
    cost_type = str(cost_type).strip()

    key = f"rg_costs:{RG_COST_CACHE_VERSION}:{year}:{month}:{cost_type}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    result = service.get_resource_group_costs(year, month, cost_type)
    cache.set(key, result, timeout=14400)  # 4 hours
    return result
