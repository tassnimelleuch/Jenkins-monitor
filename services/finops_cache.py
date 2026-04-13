from __future__ import annotations

from extensions import cache


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

    key = f"daily_cost_chart:{year}:{month}:{mode}:{only}"
    cached = cache.get(key)
    if cached is not None:
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

    key = f"rg_costs:{year}:{month}:{cost_type}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    result = service.get_resource_group_costs(year, month, cost_type)
    cache.set(key, result, timeout=14400)  # 4 hours
    return result