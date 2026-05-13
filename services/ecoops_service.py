from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from collectors.prometheus_collector import query, query_range
from services.prometheus_queries import (
    CLUSTER_NODE_CPU_PCT_QUERY,
    CLUSTER_NODE_RAM_PCT_QUERY,
    CLUSTER_RAM_TOTAL_BYTES_QUERY,
    CLUSTER_VCPUS_QUERY,
    DEFAULT_HISTORY_STEP,
    VM_CPU_PCT_QUERY,
    VM_RAM_PCT_HISTORY_QUERY,
    VM_RAM_TOTAL_BYTES_QUERY,
    VM_VCPUS_QUERY,
    now_range_iso,
)


MIN_CPU_WATTS = 0.74
CPU_WATTS_DELTA = 2.76
RAM_WATTS_PER_GB = 0.38
PUE = 1.125
GRID_INTENSITY_G_PER_KWH = 56
WINDOW_MINUTES = 60
STEP_SECONDS = 60
MONTH_HOURS = 730

def _point_map(points: List[List[float]]) -> Dict[int, float]:
    return {
        int(round(float(ts))): float(value)
        for ts, value in (points or [])
        if value is not None
    }


def _compute_power(cpu_pct: float, ram_used_gb: float, vcpus: float) -> dict:
    cpu_power = vcpus * (MIN_CPU_WATTS + (cpu_pct / 100.0) * CPU_WATTS_DELTA)
    ram_power = ram_used_gb * RAM_WATTS_PER_GB
    raw_power = cpu_power + ram_power
    wall_power = raw_power * PUE
    co2_hour = (wall_power / 1000.0) * GRID_INTENSITY_G_PER_KWH
    return {
        "cpu_power_w": cpu_power,
        "ram_power_w": ram_power,
        "raw_power_w": raw_power,
        "wall_power_w": wall_power,
        "co2_hour_g": co2_hour,
        "co2_day_g": co2_hour * 24.0,
        "co2_month_g": co2_hour * MONTH_HOURS,
    }


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _last(values: List[Optional[float]]) -> Optional[float]:
    for value in reversed(values or []):
        if value is not None:
            return value
    return None


def _build_resource_series(
    cpu_history: List[List[float]],
    ram_pct_history: List[List[float]],
    ram_total_gb: float,
    vcpus: float,
) -> dict:
    cpu_map = _point_map(cpu_history)
    ram_map = _point_map(ram_pct_history)
    timestamps = sorted(set(cpu_map) & set(ram_map))

    cpu_pct_series: List[float] = []
    ram_used_gb_series: List[float] = []
    wall_power_series: List[float] = []
    co2_hour_series: List[float] = []

    for ts in timestamps:
        cpu_pct = cpu_map[ts]
        ram_used_gb = ram_total_gb * (ram_map[ts] / 100.0)
        power = _compute_power(cpu_pct, ram_used_gb, vcpus)

        cpu_pct_series.append(_round(cpu_pct, 2))
        ram_used_gb_series.append(_round(ram_used_gb, 3))
        wall_power_series.append(_round(power["wall_power_w"], 3))
        co2_hour_series.append(_round(power["co2_hour_g"], 4))

    last_cpu_pct = _last(cpu_pct_series) or 0.0
    last_ram_used_gb = _last(ram_used_gb_series) or 0.0
    current = _compute_power(last_cpu_pct, last_ram_used_gb, vcpus)
    interval_hours = STEP_SECONDS / 3600.0
    last_hour_total = sum((value or 0.0) * interval_hours for value in co2_hour_series)

    return {
        "timestamps": timestamps,
        "cpu_pct": cpu_pct_series,
        "ram_used_gb": ram_used_gb_series,
        "wall_power_w": wall_power_series,
        "co2_hour_g": co2_hour_series,
        "summary": {
            "vcpus": _round(vcpus, 2),
            "ram_total_gb": _round(ram_total_gb, 2),
            "cpu_pct": _round(last_cpu_pct, 2),
            "ram_used_gb": _round(last_ram_used_gb, 2),
            "wall_power_w": _round(current["wall_power_w"], 2),
            "co2_hour_g": _round(current["co2_hour_g"], 3),
            "co2_day_g": _round(current["co2_day_g"], 2),
            "co2_month_g": _round(current["co2_month_g"], 2),
            "co2_last_hour_g": _round(last_hour_total, 3),
        },
    }


def _merge_series(aks: dict, vm: dict) -> dict:
    aks_co2 = dict(zip(aks["timestamps"], aks["co2_hour_g"]))
    vm_co2 = dict(zip(vm["timestamps"], vm["co2_hour_g"]))
    aks_power = dict(zip(aks["timestamps"], aks["wall_power_w"]))
    vm_power = dict(zip(vm["timestamps"], vm["wall_power_w"]))

    timestamps = sorted(set(aks_co2) | set(vm_co2))
    aks_series: List[Optional[float]] = []
    vm_series: List[Optional[float]] = []
    combined_series: List[float] = []
    combined_power_series: List[float] = []

    for ts in timestamps:
        aks_value = aks_co2.get(ts)
        vm_value = vm_co2.get(ts)
        aks_series.append(aks_value)
        vm_series.append(vm_value)
        combined_series.append(_round((aks_value or 0.0) + (vm_value or 0.0), 4))
        combined_power_series.append(
            _round((aks_power.get(ts) or 0.0) + (vm_power.get(ts) or 0.0), 3)
        )

    return {
        "timestamps": timestamps,
        "aks_co2_hour_g": aks_series,
        "vm_co2_hour_g": vm_series,
        "combined_co2_hour_g": combined_series,
        "combined_wall_power_w": combined_power_series,
    }


def get_ecoops_metrics() -> dict:
    try:
        start, end = now_range_iso(WINDOW_MINUTES)

        vm_vcpus = query(VM_VCPUS_QUERY)
        vm_ram_total_bytes = query(VM_RAM_TOTAL_BYTES_QUERY)
        cluster_vcpus = query(CLUSTER_VCPUS_QUERY)
        cluster_ram_total_bytes = query(CLUSTER_RAM_TOTAL_BYTES_QUERY)

        vm_cpu_history = query_range(VM_CPU_PCT_QUERY, start, end, step=DEFAULT_HISTORY_STEP)
        vm_ram_history = query_range(
            VM_RAM_PCT_HISTORY_QUERY, start, end, step=DEFAULT_HISTORY_STEP
        )
        cluster_cpu_history = query_range(
            CLUSTER_NODE_CPU_PCT_QUERY,
            start,
            end,
            step=DEFAULT_HISTORY_STEP,
        )
        cluster_ram_history = query_range(
            CLUSTER_NODE_RAM_PCT_QUERY,
            start,
            end,
            step=DEFAULT_HISTORY_STEP,
        )

        if not all(
            value is not None
            for value in (
                vm_vcpus,
                vm_ram_total_bytes,
                cluster_vcpus,
                cluster_ram_total_bytes,
            )
        ):
            return {
                "connected": False,
                "error": "Missing VM or AKS capacity metrics from Prometheus.",
            }

        vm = _build_resource_series(
            vm_cpu_history,
            vm_ram_history,
            float(vm_ram_total_bytes) / 1e9,
            float(vm_vcpus),
        )
        aks = _build_resource_series(
            cluster_cpu_history,
            cluster_ram_history,
            float(cluster_ram_total_bytes) / 1e9,
            float(cluster_vcpus),
        )

        if not vm["timestamps"] and not aks["timestamps"]:
            return {
                "connected": False,
                "error": "Prometheus returned no EcoOps history for the last hour.",
            }

        series = _merge_series(aks, vm)
        combined_summary = {
            "wall_power_w": _round(
                (aks["summary"]["wall_power_w"] or 0.0)
                + (vm["summary"]["wall_power_w"] or 0.0),
                2,
            ),
            "co2_hour_g": _round(
                (aks["summary"]["co2_hour_g"] or 0.0)
                + (vm["summary"]["co2_hour_g"] or 0.0),
                3,
            ),
            "co2_day_g": _round(
                (aks["summary"]["co2_day_g"] or 0.0)
                + (vm["summary"]["co2_day_g"] or 0.0),
                2,
            ),
            "co2_month_g": _round(
                (aks["summary"]["co2_month_g"] or 0.0)
                + (vm["summary"]["co2_month_g"] or 0.0),
                2,
            ),
            "co2_last_hour_g": _round(
                (aks["summary"]["co2_last_hour_g"] or 0.0)
                + (vm["summary"]["co2_last_hour_g"] or 0.0),
                3,
            ),
        }

        return {
            "connected": True,
            "window_minutes": WINDOW_MINUTES,
            "step_seconds": STEP_SECONDS,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "formulas": {
                "min_cpu_watts": MIN_CPU_WATTS,
                "cpu_watts_delta": CPU_WATTS_DELTA,
                "ram_watts_per_gb": RAM_WATTS_PER_GB,
                "pue": PUE,
                "grid_intensity_g_per_kwh": GRID_INTENSITY_G_PER_KWH,
            },
            "summary": {
                "aks": aks["summary"],
                "vm": vm["summary"],
                "combined": combined_summary,
            },
            "series": series,
        }
    except Exception as exc:
        return {
            "connected": False,
            "error": f"EcoOps metrics failed: {type(exc).__name__}: {exc}",
        }
