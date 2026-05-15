from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import threading
from typing import Dict, Iterable, Optional, Tuple

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from collectors.azure_cost_collector import AzureCostProvider
from extensions import cache, db
from finops_models import (
    FinOpsDailyCost,
    FinOpsResourceGroupMonthlyCost,
    FinOpsSyncState,
)
from services.finops_service import FinOpsService


DAILY_COST_CACHE_VERSION = "v3"
RG_COST_CACHE_VERSION = "v2"
FINOPS_CACHE_TIMEOUT_SECONDS = 14400
DEFAULT_FINOPS_SYNC_INTERVAL_SECONDS = 1800
DAILY_COST_FILTERS = ("all", "aks", "vm", "subscription")
DAILY_COST_MODES = ("actual", "forecast")
RESOURCE_GROUP_COST_TYPES = ("ActualCost", "AmortizedCost")
DAILY_DATASET = "daily_costs"
RESOURCE_GROUP_DATASET = "resource_group_costs"

_finops_refresh_lock = threading.Lock()
_finops_refresh_in_progress = set()


def _utcnow():
    return datetime.now(timezone.utc)


def _month_bounds(year: int, month: int) -> Tuple[date, date]:
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _month_days(year: int, month: int):
    _, last_day = _month_bounds(year, month)
    for day in range(1, last_day.day + 1):
        yield date(year, month, day)


def _previous_month(year: int, month: int) -> Tuple[int, int]:
    return FinOpsService._previous_month(year, month)


def _next_month(year: int, month: int) -> Tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _normalize_mode(mode: str) -> str:
    clean_mode = str(mode or "").strip().lower()
    if clean_mode not in DAILY_COST_MODES:
        raise ValueError("Invalid mode. Use actual or forecast.")
    return clean_mode


def _normalize_only(only: str) -> str:
    clean_only = str(only or "").strip().lower()
    if clean_only not in DAILY_COST_FILTERS:
        raise ValueError("Invalid only filter. Use all, aks, vm, or subscription.")
    return clean_only


def _normalize_cost_type(cost_type: str) -> str:
    clean_cost_type = str(cost_type or "").strip()
    if clean_cost_type not in RESOURCE_GROUP_COST_TYPES:
        raise ValueError("Invalid cost_type. Use ActualCost or AmortizedCost.")
    return clean_cost_type


def _daily_cost_cache_key(year: int, month: int, mode: str, only: str) -> str:
    return f"daily_cost_chart:{DAILY_COST_CACHE_VERSION}:{year}:{month}:{mode}:{only}"


def _resource_group_cache_key(year: int, month: int, cost_type: str) -> str:
    return f"rg_costs:{RG_COST_CACHE_VERSION}:{year}:{month}:{cost_type}"


def _clear_daily_cost_cache(year: int, month: int, mode: str):
    for only in DAILY_COST_FILTERS:
        cache.delete(_daily_cost_cache_key(year, month, mode, only))


def _clear_resource_group_cache(year: int, month: int, cost_type: str):
    cache.delete(_resource_group_cache_key(year, month, cost_type))


def _to_float(value) -> float:
    return round(float(value or 0.0), 4)


def _sync_state_key(dataset: str, subscription_id: str, year: int, month: int, scope_key: str) -> str:
    return f"{dataset}:{subscription_id}:{year}:{month}:{scope_key}"


def _build_live_service(subscription_id: str) -> FinOpsService:
    provider = AzureCostProvider(subscription_id=subscription_id)
    return FinOpsService(provider)


def _finops_sync_interval_seconds() -> int:
    return int(
        current_app.config.get(
            "FINOPS_SYNC_INTERVAL_SECONDS",
            DEFAULT_FINOPS_SYNC_INTERVAL_SECONDS,
        )
    )


def _daily_sync_state(
    subscription_id: str,
    year: int,
    month: int,
    mode: str,
) -> Optional[FinOpsSyncState]:
    return FinOpsSyncState.query.filter_by(
        subscription_id=subscription_id,
        dataset=DAILY_DATASET,
        year=year,
        month=month,
        scope_key=mode,
    ).one_or_none()


def _resource_group_sync_state(
    subscription_id: str,
    year: int,
    month: int,
    cost_type: str,
) -> Optional[FinOpsSyncState]:
    return FinOpsSyncState.query.filter_by(
        subscription_id=subscription_id,
        dataset=RESOURCE_GROUP_DATASET,
        year=year,
        month=month,
        scope_key=cost_type,
    ).one_or_none()


def _ensure_sync_state(
    subscription_id: str,
    dataset: str,
    year: int,
    month: int,
    scope_key: str,
) -> FinOpsSyncState:
    row = FinOpsSyncState.query.filter_by(
        subscription_id=subscription_id,
        dataset=dataset,
        year=year,
        month=month,
        scope_key=scope_key,
    ).one_or_none()
    if row is None:
        row = FinOpsSyncState(
            subscription_id=subscription_id,
            dataset=dataset,
            year=year,
            month=month,
            scope_key=scope_key,
        )
        db.session.add(row)
    return row


def _record_sync_failure(
    subscription_id: str,
    dataset: str,
    year: int,
    month: int,
    scope_key: str,
    exc: Exception,
):
    try:
        now = _utcnow()
        row = _ensure_sync_state(subscription_id, dataset, year, month, scope_key)
        row.last_attempted_at = now
        row.last_error = f"{type(exc).__name__}: {exc}"
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to persist FinOps sync failure state for %s/%s.",
            dataset,
            scope_key,
        )


def _sync_is_due(state: Optional[FinOpsSyncState], max_age_seconds: Optional[int] = None) -> bool:
    if state is None:
        return True

    if max_age_seconds is None:
        max_age_seconds = _finops_sync_interval_seconds()

    reference_time = state.last_synced_at or state.last_attempted_at
    if reference_time is None:
        return True

    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)

    return (_utcnow() - reference_time) >= timedelta(seconds=max_age_seconds)


def _build_daily_payload_rows(
    year: int,
    month: int,
    stored_rows: Iterable[FinOpsDailyCost],
):
    day_map = {
        day.isoformat(): {
            "aks": 0.0,
            "vm": 0.0,
            "other": 0.0,
        }
        for day in _month_days(year, month)
    }

    last_synced_at = None
    row_count = 0

    for row in stored_rows:
        day_key = row.usage_date.isoformat()
        if day_key not in day_map:
            continue
        day_map[day_key]["aks"] = _to_float(row.aks_cost)
        day_map[day_key]["vm"] = _to_float(row.vm_cost)
        day_map[day_key]["other"] = _to_float(row.other_cost)
        row_count += 1

        row_synced_at = row.last_synced_at
        if row_synced_at is not None and row_synced_at.tzinfo is None:
            row_synced_at = row_synced_at.replace(tzinfo=timezone.utc)
        if row_synced_at is not None and (last_synced_at is None or row_synced_at > last_synced_at):
            last_synced_at = row_synced_at

    return day_map, row_count, last_synced_at


def _build_display_rows(
    year: int,
    month: int,
    day_map: Dict[str, Dict[str, float]],
    only: str,
):
    rows = []
    subscription_totals = []

    for day in _month_days(year, month):
        day_key = day.isoformat()
        buckets = day_map.get(day_key) or {}
        aks_val = round(float(buckets.get("aks", 0.0) or 0.0), 4)
        vm_val = round(float(buckets.get("vm", 0.0) or 0.0), 4)
        other_val = round(float(buckets.get("other", 0.0) or 0.0), 4)
        subscription_total = round(aks_val + vm_val + other_val, 4)
        subscription_totals.append(subscription_total)

        if only == "aks":
            aks_cost, vm_cost, total_cost = aks_val, 0.0, aks_val
        elif only == "vm":
            aks_cost, vm_cost, total_cost = 0.0, vm_val, vm_val
        elif only == "subscription":
            aks_cost, vm_cost, total_cost = aks_val, vm_val, subscription_total
        else:
            aks_cost, vm_cost, total_cost = aks_val, vm_val, subscription_total

        rows.append(
            {
                "day": day_key,
                "aks": aks_cost,
                "vm": vm_cost,
                "total": total_cost,
            }
        )

    return rows, subscription_totals


def _build_summary(rows):
    total_cost = sum(row["total"] for row in rows)
    aks_total = sum(row["aks"] for row in rows)
    vm_total = sum(row["vm"] for row in rows)
    avg_daily_cost = total_cost / len(rows) if rows else 0.0
    highest_day = max(rows, key=lambda row: row["total"]) if rows else None

    return {
        "total_cost": round(total_cost, 2),
        "aks_total": round(aks_total, 2),
        "vm_total": round(vm_total, 2),
        "average_daily_cost": round(avg_daily_cost, 2),
        "highest_day": highest_day["day"] if highest_day else None,
        "highest_day_cost": round(highest_day["total"], 2) if highest_day else 0.0,
    }


def _daily_storage_query(subscription_id: str, year: int, month: int, mode: str):
    start_date, end_date = _month_bounds(year, month)
    return (
        FinOpsDailyCost.query
        .filter(
            FinOpsDailyCost.subscription_id == subscription_id,
            FinOpsDailyCost.cost_mode == mode,
            FinOpsDailyCost.usage_date >= start_date,
            FinOpsDailyCost.usage_date <= end_date,
        )
        .order_by(FinOpsDailyCost.usage_date.asc())
    )


def _resource_group_storage_query(
    subscription_id: str,
    year: int,
    month: int,
    cost_type: str,
):
    return (
        FinOpsResourceGroupMonthlyCost.query
        .filter_by(
            subscription_id=subscription_id,
            year=year,
            month=month,
            cost_type=cost_type,
        )
        .order_by(FinOpsResourceGroupMonthlyCost.total_cost.desc())
    )


def get_stored_daily_cost_chart(
    subscription_id: str,
    year: int,
    month: int,
    mode: str = "actual",
    only: str = "all",
):
    year = int(year)
    month = int(month)
    mode = _normalize_mode(mode)
    only = _normalize_only(only)

    current_rows = _daily_storage_query(subscription_id, year, month, mode).all()
    if not current_rows:
        return None

    prev_year, prev_month = _previous_month(year, month)
    previous_rows = _daily_storage_query(subscription_id, prev_year, prev_month, mode).all()

    current_map, row_count, last_synced_at = _build_daily_payload_rows(year, month, current_rows)
    previous_map, _, _ = _build_daily_payload_rows(prev_year, prev_month, previous_rows)

    current_display_rows, subscription_totals = _build_display_rows(year, month, current_map, only)
    previous_display_rows, prev_subscription_totals = _build_display_rows(
        prev_year,
        prev_month,
        previous_map,
        only,
    )

    current_summary = _build_summary(current_display_rows)
    previous_summary = _build_summary(previous_display_rows)

    totals = [row["total"] for row in current_display_rows]
    if len(totals) < 14:
        totals = [row["total"] for row in previous_display_rows][-7:] + totals

    previous_week_change = FinOpsService._compute_previous_week_change_from_totals(totals)
    previous_month_label = f"{prev_year}-{prev_month:02d}"

    return {
        "year": year,
        "month": month,
        "mode": mode,
        "only": only,
        "labels": [row["day"] for row in current_display_rows],
        "series": {
            "aks": [row["aks"] for row in current_display_rows],
            "vm": [row["vm"] for row in current_display_rows],
            "subscription_total": subscription_totals if only == "subscription" else [],
            "previous_month_subscription_total": (
                prev_subscription_totals if only == "subscription" else []
            ),
        },
        "meta": {
            "source": "database",
            "row_count": row_count,
            "date_col": "usage_date",
            "type_col": "resource_type_bucket",
            "cost_col": "total_cost",
            "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        },
        "summary": {
            **current_summary,
            "previous_week_change_pct": (
                round(previous_week_change, 2)
                if previous_week_change is not None
                else None
            ),
            "previous_month_label": previous_month_label,
            "previous_month": previous_summary,
            "delta": {
                "total_cost": FinOpsService._compute_change(
                    current_summary["total_cost"],
                    previous_summary["total_cost"],
                ),
                "aks_total": FinOpsService._compute_change(
                    current_summary["aks_total"],
                    previous_summary["aks_total"],
                ),
                "vm_total": FinOpsService._compute_change(
                    current_summary["vm_total"],
                    previous_summary["vm_total"],
                ),
                "average_daily_cost": FinOpsService._compute_change(
                    current_summary["average_daily_cost"],
                    previous_summary["average_daily_cost"],
                ),
                "highest_day_cost": FinOpsService._compute_change(
                    current_summary["highest_day_cost"],
                    previous_summary["highest_day_cost"],
                ),
            },
        },
    }


def get_cached_stored_daily_cost_chart(
    subscription_id: str,
    year: int,
    month: int,
    mode: str = "actual",
    only: str = "all",
):
    year = int(year)
    month = int(month)
    mode = _normalize_mode(mode)
    only = _normalize_only(only)

    key = _daily_cost_cache_key(year, month, mode, only)
    cached = cache.get(key)
    if cached is not None:
        return cached

    stored = get_stored_daily_cost_chart(subscription_id, year, month, mode, only)
    if stored is not None:
        cache.set(key, stored, timeout=FINOPS_CACHE_TIMEOUT_SECONDS)
    return stored


def get_stored_resource_group_costs(
    subscription_id: str,
    year: int,
    month: int,
    cost_type: str = "ActualCost",
):
    year = int(year)
    month = int(month)
    cost_type = _normalize_cost_type(cost_type)

    rows = _resource_group_storage_query(subscription_id, year, month, cost_type).all()
    state = _resource_group_sync_state(subscription_id, year, month, cost_type)
    if not rows and (state is None or state.last_synced_at is None):
        return None

    last_synced_at = state.last_synced_at if state is not None else None
    total_cost = round(sum(_to_float(row.total_cost) for row in rows), 2)

    return {
        "year": year,
        "month": month,
        "cost_type": cost_type,
        "total_cost": total_cost,
        "meta": {
            "source": "database",
            "row_count": len(rows),
            "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        },
        "resource_groups": [
            {
                "name": row.resource_group_name,
                "total": round(_to_float(row.total_cost), 2),
                "aks": round(_to_float(row.aks_cost), 2),
                "vm": round(_to_float(row.vm_cost), 2),
                "other": round(_to_float(row.other_cost), 2),
                "by_resource_type": row.by_resource_type or {},
            }
            for row in rows
        ],
    }


def get_cached_stored_resource_group_costs(
    subscription_id: str,
    year: int,
    month: int,
    cost_type: str = "ActualCost",
):
    year = int(year)
    month = int(month)
    cost_type = _normalize_cost_type(cost_type)

    key = _resource_group_cache_key(year, month, cost_type)
    cached = cache.get(key)
    if cached is not None:
        return cached

    stored = get_stored_resource_group_costs(subscription_id, year, month, cost_type)
    if stored is not None:
        cache.set(key, stored, timeout=FINOPS_CACHE_TIMEOUT_SECONDS)
    return stored


def warm_daily_cost_cache(subscription_id: str, year: int, month: int, mode: str):
    mode = _normalize_mode(mode)
    for only in DAILY_COST_FILTERS:
        key = _daily_cost_cache_key(year, month, mode, only)
        payload = get_stored_daily_cost_chart(subscription_id, year, month, mode, only)
        if payload is None:
            cache.delete(key)
            continue
        cache.set(key, payload, timeout=FINOPS_CACHE_TIMEOUT_SECONDS)


def warm_resource_group_cache(subscription_id: str, year: int, month: int, cost_type: str):
    cost_type = _normalize_cost_type(cost_type)
    key = _resource_group_cache_key(year, month, cost_type)
    payload = get_stored_resource_group_costs(subscription_id, year, month, cost_type)
    if payload is None:
        cache.delete(key)
        return
    cache.set(key, payload, timeout=FINOPS_CACHE_TIMEOUT_SECONDS)


def sync_finops_daily_cost_month(
    subscription_id: str,
    year: int,
    month: int,
    mode: str = "actual",
    *,
    service: Optional[FinOpsService] = None,
    force: bool = False,
):
    year = int(year)
    month = int(month)
    mode = _normalize_mode(mode)

    state = _daily_sync_state(subscription_id, year, month, mode)
    if not force and not _sync_is_due(state):
        return False

    live_service = service or _build_live_service(subscription_id)
    snapshot = live_service.get_daily_cost_storage_snapshot(year, month, mode)
    now = _utcnow()
    start_date, end_date = _month_bounds(year, month)

    try:
        existing_rows = {
            row.usage_date: row
            for row in FinOpsDailyCost.query.filter(
                FinOpsDailyCost.subscription_id == subscription_id,
                FinOpsDailyCost.cost_mode == mode,
                FinOpsDailyCost.usage_date >= start_date,
                FinOpsDailyCost.usage_date <= end_date,
            ).all()
        }

        incoming_dates = set()
        for item in snapshot.get("rows") or []:
            usage_date = date.fromisoformat(item["day"])
            incoming_dates.add(usage_date)

            row = existing_rows.get(usage_date)
            if row is None:
                row = FinOpsDailyCost(
                    subscription_id=subscription_id,
                    usage_date=usage_date,
                    cost_mode=mode,
                )
                db.session.add(row)

            row.currency_code = snapshot.get("currency_code") or "USD"
            row.aks_cost = item.get("aks_cost", 0)
            row.vm_cost = item.get("vm_cost", 0)
            row.other_cost = item.get("other_cost", 0)
            row.total_cost = item.get("total_cost", 0)
            row.source_system = "azure_cost_management"
            row.last_synced_at = now

        for usage_date, row in existing_rows.items():
            if usage_date not in incoming_dates:
                db.session.delete(row)

        sync_state = _ensure_sync_state(
            subscription_id,
            DAILY_DATASET,
            year,
            month,
            mode,
        )
        sync_state.last_attempted_at = now
        sync_state.last_synced_at = now
        sync_state.last_error = None

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _record_sync_failure(subscription_id, DAILY_DATASET, year, month, mode, exc)
        current_app.logger.exception(
            "Failed to sync FinOps daily costs for %s %04d-%02d (%s).",
            subscription_id,
            year,
            month,
            mode,
        )
        raise

    warm_daily_cost_cache(subscription_id, year, month, mode)

    current_month = _utcnow()
    if mode == "actual" and year == current_month.year and month == current_month.month:
        try:
            from services.alerts_service import sync_finops_daily_cost_threshold_alerts

            sync_finops_daily_cost_threshold_alerts(subscription_id, year, month)
        except Exception:
            current_app.logger.exception(
                "Failed to refresh FinOps daily threshold alerts for %04d-%02d.",
                year,
                month,
            )

    next_year, next_month = _next_month(year, month)
    _clear_daily_cost_cache(next_year, next_month, mode)
    warm_daily_cost_cache(subscription_id, next_year, next_month, mode)
    return True


def sync_finops_resource_group_month(
    subscription_id: str,
    year: int,
    month: int,
    cost_type: str = "ActualCost",
    *,
    service: Optional[FinOpsService] = None,
    force: bool = False,
):
    year = int(year)
    month = int(month)
    cost_type = _normalize_cost_type(cost_type)

    state = _resource_group_sync_state(subscription_id, year, month, cost_type)
    if not force and not _sync_is_due(state):
        return False

    live_service = service or _build_live_service(subscription_id)
    snapshot = live_service.get_resource_group_cost_storage_snapshot(year, month, cost_type)
    now = _utcnow()

    try:
        existing_rows = {
            row.resource_group_name: row
            for row in _resource_group_storage_query(subscription_id, year, month, cost_type).all()
        }

        incoming_names = set()
        for item in snapshot.get("resource_groups") or []:
            resource_group_name = str(item.get("name") or "").strip()
            if not resource_group_name:
                continue

            incoming_names.add(resource_group_name)
            row = existing_rows.get(resource_group_name)
            if row is None:
                row = FinOpsResourceGroupMonthlyCost(
                    subscription_id=subscription_id,
                    year=year,
                    month=month,
                    cost_type=cost_type,
                    resource_group_name=resource_group_name,
                )
                db.session.add(row)

            row.currency_code = snapshot.get("currency_code") or "USD"
            row.aks_cost = item.get("aks", 0)
            row.vm_cost = item.get("vm", 0)
            row.other_cost = item.get("other", 0)
            row.total_cost = item.get("total", 0)
            row.by_resource_type = item.get("by_resource_type") or {}
            row.source_system = "azure_cost_management"
            row.last_synced_at = now

        for resource_group_name, row in existing_rows.items():
            if resource_group_name not in incoming_names:
                db.session.delete(row)

        sync_state = _ensure_sync_state(
            subscription_id,
            RESOURCE_GROUP_DATASET,
            year,
            month,
            cost_type,
        )
        sync_state.last_attempted_at = now
        sync_state.last_synced_at = now
        sync_state.last_error = None

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _record_sync_failure(
            subscription_id,
            RESOURCE_GROUP_DATASET,
            year,
            month,
            cost_type,
            exc,
        )
        current_app.logger.exception(
            "Failed to sync FinOps resource groups for %s %04d-%02d (%s).",
            subscription_id,
            year,
            month,
            cost_type,
        )
        raise

    warm_resource_group_cache(subscription_id, year, month, cost_type)
    return True


def _run_async_refresh(app, refresh_key: str, task):
    try:
        with app.app_context():
            task()
    except Exception:
        app.logger.exception("Background FinOps refresh failed for %s.", refresh_key)
    finally:
        with _finops_refresh_lock:
            _finops_refresh_in_progress.discard(refresh_key)


def _start_async_refresh(refresh_key: str, task):
    with _finops_refresh_lock:
        if refresh_key in _finops_refresh_in_progress:
            return False
        _finops_refresh_in_progress.add(refresh_key)

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_async_refresh,
        args=(app, refresh_key, task),
        daemon=True,
        name=f"finops-refresh-{refresh_key}",
    )
    thread.start()
    return True


def schedule_daily_cost_sync_if_due(
    subscription_id: str,
    year: int,
    month: int,
    mode: str = "actual",
):
    year = int(year)
    month = int(month)
    mode = _normalize_mode(mode)

    state = _daily_sync_state(subscription_id, year, month, mode)
    if not _sync_is_due(state):
        return False

    refresh_key = _sync_state_key(DAILY_DATASET, subscription_id, year, month, mode)
    return _start_async_refresh(
        refresh_key,
        lambda: sync_finops_daily_cost_month(
            subscription_id,
            year,
            month,
            mode,
            force=True,
        ),
    )


def schedule_resource_group_sync_if_due(
    subscription_id: str,
    year: int,
    month: int,
    cost_type: str = "ActualCost",
):
    year = int(year)
    month = int(month)
    cost_type = _normalize_cost_type(cost_type)

    state = _resource_group_sync_state(subscription_id, year, month, cost_type)
    if not _sync_is_due(state):
        return False

    refresh_key = _sync_state_key(
        RESOURCE_GROUP_DATASET,
        subscription_id,
        year,
        month,
        cost_type,
    )
    return _start_async_refresh(
        refresh_key,
        lambda: sync_finops_resource_group_month(
            subscription_id,
            year,
            month,
            cost_type,
            force=True,
        ),
    )


def get_finops_daily_cost_chart(
    subscription_id: str,
    year: int,
    month: int,
    mode: str = "actual",
    only: str = "all",
    *,
    service: Optional[FinOpsService] = None,
):
    year = int(year)
    month = int(month)
    mode = _normalize_mode(mode)
    only = _normalize_only(only)

    stored = get_cached_stored_daily_cost_chart(subscription_id, year, month, mode, only)
    prev_year, prev_month = _previous_month(year, month)

    if stored is not None:
        schedule_daily_cost_sync_if_due(subscription_id, year, month, mode)
        schedule_daily_cost_sync_if_due(subscription_id, prev_year, prev_month, mode)
        return stored

    live_service = service or _build_live_service(subscription_id)
    sync_finops_daily_cost_month(
        subscription_id,
        year,
        month,
        mode,
        service=live_service,
        force=True,
    )
    try:
        sync_finops_daily_cost_month(
            subscription_id,
            prev_year,
            prev_month,
            mode,
            service=live_service,
            force=True,
        )
    except Exception:
        current_app.logger.exception(
            "FinOps previous-month backfill failed for %04d-%02d (%s).",
            prev_year,
            prev_month,
            mode,
        )

    stored = get_cached_stored_daily_cost_chart(subscription_id, year, month, mode, only)
    if stored is not None:
        return stored

    raise RuntimeError("No FinOps daily cost data is available yet for the requested month.")


def get_finops_resource_group_costs(
    subscription_id: str,
    year: int,
    month: int,
    cost_type: str = "ActualCost",
    *,
    service: Optional[FinOpsService] = None,
):
    year = int(year)
    month = int(month)
    cost_type = _normalize_cost_type(cost_type)

    stored = get_cached_stored_resource_group_costs(
        subscription_id,
        year,
        month,
        cost_type,
    )
    if stored is not None:
        schedule_resource_group_sync_if_due(subscription_id, year, month, cost_type)
        return stored

    live_service = service or _build_live_service(subscription_id)
    sync_finops_resource_group_month(
        subscription_id,
        year,
        month,
        cost_type,
        service=live_service,
        force=True,
    )

    stored = get_cached_stored_resource_group_costs(
        subscription_id,
        year,
        month,
        cost_type,
    )
    if stored is not None:
        return stored

    raise RuntimeError("No FinOps resource-group cost data is available yet for the requested month.")


def refresh_finops_month(
    subscription_id: str,
    year: int,
    month: int,
    *,
    daily_modes: Iterable[str] = DAILY_COST_MODES,
    resource_group_cost_types: Iterable[str] = ("ActualCost",),
    force: bool = True,
):
    year = int(year)
    month = int(month)

    live_service = _build_live_service(subscription_id)
    prev_year, prev_month = _previous_month(year, month)

    results = {
        "daily_costs": {},
        "resource_group_costs": {},
    }

    for mode in daily_modes:
        clean_mode = _normalize_mode(mode)
        sync_finops_daily_cost_month(
            subscription_id,
            prev_year,
            prev_month,
            clean_mode,
            service=live_service,
            force=force,
        )
        changed = sync_finops_daily_cost_month(
            subscription_id,
            year,
            month,
            clean_mode,
            service=live_service,
            force=force,
        )
        results["daily_costs"][clean_mode] = {
            "synced": True,
            "changed": bool(changed),
        }

    for cost_type in resource_group_cost_types:
        clean_cost_type = _normalize_cost_type(cost_type)
        changed = sync_finops_resource_group_month(
            subscription_id,
            year,
            month,
            clean_cost_type,
            service=live_service,
            force=force,
        )
        results["resource_group_costs"][clean_cost_type] = {
            "synced": True,
            "changed": bool(changed),
        }

    return results
