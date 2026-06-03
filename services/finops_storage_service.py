from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import threading
from typing import Iterable, Optional, Tuple

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from collectors.azure_cost_collector import AzureCostProvider
from extensions import cache, db
from finops_models import FinOpsDailyCost, FinOpsSyncState
from services.finops_service import FinOpsService


DAILY_COST_CACHE_VERSION = 'v5'
FINOPS_CACHE_TIMEOUT_SECONDS = 14400
DEFAULT_FINOPS_SYNC_INTERVAL_SECONDS = 1800
DAILY_DATASET = 'daily_costs'
DAILY_SCOPE_KEY = 'actual_total_v2'

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


def _daily_cost_cache_key(year: int, month: int) -> str:
    return f'daily_cost_chart:{DAILY_COST_CACHE_VERSION}:{year}:{month}'


def _clear_daily_cost_cache(year: int, month: int):
    cache.delete(_daily_cost_cache_key(year, month))


def _to_float(value) -> float:
    return round(float(value or 0.0), 4)


def _sync_state_key(subscription_id: str, year: int, month: int) -> str:
    return f'{DAILY_DATASET}:{subscription_id}:{year}:{month}:{DAILY_SCOPE_KEY}'


def _build_live_service(subscription_id: str) -> FinOpsService:
    provider = AzureCostProvider(subscription_id=subscription_id)
    return FinOpsService(provider)


def _finops_sync_interval_seconds() -> int:
    return int(
        current_app.config.get(
            'FINOPS_SYNC_INTERVAL_SECONDS',
            DEFAULT_FINOPS_SYNC_INTERVAL_SECONDS,
        )
    )


def _daily_sync_state(
    subscription_id: str,
    year: int,
    month: int,
) -> Optional[FinOpsSyncState]:
    return FinOpsSyncState.query.filter_by(
        subscription_id=subscription_id,
        dataset=DAILY_DATASET,
        year=year,
        month=month,
        scope_key=DAILY_SCOPE_KEY,
    ).one_or_none()


def _ensure_sync_state(
    subscription_id: str,
    year: int,
    month: int,
) -> FinOpsSyncState:
    row = _daily_sync_state(subscription_id, year, month)
    if row is None:
        row = FinOpsSyncState(
            subscription_id=subscription_id,
            dataset=DAILY_DATASET,
            year=year,
            month=month,
            scope_key=DAILY_SCOPE_KEY,
        )
        db.session.add(row)
    return row


def _record_sync_failure(
    subscription_id: str,
    year: int,
    month: int,
    exc: Exception,
):
    try:
        now = _utcnow()
        row = _ensure_sync_state(subscription_id, year, month)
        row.last_attempted_at = now
        row.last_error = f'{type(exc).__name__}: {exc}'
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to persist FinOps sync failure state for %04d-%02d.',
            year,
            month,
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


def _state_needs_sync(state: Optional[FinOpsSyncState]) -> bool:
    return state is None or state.last_synced_at is None or _sync_is_due(state)


def _daily_storage_query(subscription_id: str, year: int, month: int):
    start_date, end_date = _month_bounds(year, month)
    return (
        FinOpsDailyCost.query
        .filter(
            FinOpsDailyCost.subscription_id == subscription_id,
            FinOpsDailyCost.usage_date >= start_date,
            FinOpsDailyCost.usage_date <= end_date,
        )
        .order_by(FinOpsDailyCost.usage_date.asc())
    )


def _rows_to_day_payload(year: int, month: int, stored_rows: Iterable[FinOpsDailyCost]):
    day_map = {day.isoformat(): 0.0 for day in _month_days(year, month)}
    last_synced_at = None
    row_count = 0

    for row in stored_rows:
        day_key = row.usage_date.isoformat()
        if day_key not in day_map:
            continue
        day_map[day_key] = _to_float(row.total_cost)
        row_count += 1

        row_synced_at = row.last_synced_at
        if row_synced_at is not None and row_synced_at.tzinfo is None:
            row_synced_at = row_synced_at.replace(tzinfo=timezone.utc)
        if row_synced_at is not None and (last_synced_at is None or row_synced_at > last_synced_at):
            last_synced_at = row_synced_at

    rows = [
        {
            'day': day.isoformat(),
            'total': day_map[day.isoformat()],
        }
        for day in _month_days(year, month)
    ]
    return rows, row_count, last_synced_at


def _build_summary(rows):
    total_cost = sum(row['total'] for row in rows)
    avg_daily_cost = total_cost / len(rows) if rows else 0.0
    highest_day = max(rows, key=lambda row: row['total']) if rows else None

    return {
        'total_cost': round(total_cost, 2),
        'average_daily_cost': round(avg_daily_cost, 2),
        'highest_day': highest_day['day'] if highest_day else None,
        'highest_day_cost': round(highest_day['total'], 2) if highest_day else 0.0,
    }


def get_stored_daily_cost_chart(
    subscription_id: str,
    year: int,
    month: int,
):
    year = int(year)
    month = int(month)

    current_rows = _daily_storage_query(subscription_id, year, month).all()
    current_state = _daily_sync_state(subscription_id, year, month)
    if not current_rows and (current_state is None or current_state.last_synced_at is None):
        return None

    prev_year, prev_month = _previous_month(year, month)
    previous_rows = _daily_storage_query(subscription_id, prev_year, prev_month).all()

    current_display_rows, row_count, last_synced_at = _rows_to_day_payload(year, month, current_rows)
    previous_display_rows, _, _ = _rows_to_day_payload(prev_year, prev_month, previous_rows)

    current_summary = _build_summary(current_display_rows)
    previous_summary = _build_summary(previous_display_rows)

    totals = [row['total'] for row in current_display_rows]
    if len(totals) < 14:
        totals = [row['total'] for row in previous_display_rows][-7:] + totals

    previous_week_change = FinOpsService._compute_previous_week_change_from_totals(totals)
    previous_month_label = f'{prev_year}-{prev_month:02d}'

    return {
        'year': year,
        'month': month,
        'labels': [row['day'] for row in current_display_rows],
        'series': {
            'total': [row['total'] for row in current_display_rows],
            'previous_month_total': [row['total'] for row in previous_display_rows],
        },
        'meta': {
            'source': 'database',
            'row_count': row_count,
            'date_col': 'usage_date',
            'cost_col': 'total_cost',
            'query_scope': 'subscription_daily_total',
            'last_synced_at': last_synced_at.isoformat() if last_synced_at else None,
        },
        'summary': {
            **current_summary,
            'previous_week_change_pct': (
                round(previous_week_change, 2)
                if previous_week_change is not None
                else None
            ),
            'previous_month_label': previous_month_label,
            'previous_month': previous_summary,
            'delta': {
                'total_cost': FinOpsService._compute_change(
                    current_summary['total_cost'],
                    previous_summary['total_cost'],
                ),
                'average_daily_cost': FinOpsService._compute_change(
                    current_summary['average_daily_cost'],
                    previous_summary['average_daily_cost'],
                ),
                'highest_day_cost': FinOpsService._compute_change(
                    current_summary['highest_day_cost'],
                    previous_summary['highest_day_cost'],
                ),
            },
        },
    }


def get_cached_stored_daily_cost_chart(
    subscription_id: str,
    year: int,
    month: int,
):
    year = int(year)
    month = int(month)

    key = _daily_cost_cache_key(year, month)
    cached = cache.get(key)
    if cached is not None:
        return cached

    stored = get_stored_daily_cost_chart(subscription_id, year, month)
    if stored is not None:
        cache.set(key, stored, timeout=FINOPS_CACHE_TIMEOUT_SECONDS)
    return stored


def warm_daily_cost_cache(subscription_id: str, year: int, month: int):
    key = _daily_cost_cache_key(year, month)
    payload = get_stored_daily_cost_chart(subscription_id, year, month)
    if payload is None:
        cache.delete(key)
        return
    cache.set(key, payload, timeout=FINOPS_CACHE_TIMEOUT_SECONDS)


def sync_finops_daily_cost_month(
    subscription_id: str,
    year: int,
    month: int,
    *,
    service: Optional[FinOpsService] = None,
    force: bool = False,
):
    year = int(year)
    month = int(month)

    state = _daily_sync_state(subscription_id, year, month)
    if not force and not _sync_is_due(state):
        return False

    live_service = service or _build_live_service(subscription_id)
    snapshot = live_service.get_daily_cost_storage_snapshot(year, month)
    now = _utcnow()
    start_date, end_date = _month_bounds(year, month)

    try:
        existing_rows = {
            row.usage_date: row
            for row in FinOpsDailyCost.query.filter(
                FinOpsDailyCost.subscription_id == subscription_id,
                FinOpsDailyCost.usage_date >= start_date,
                FinOpsDailyCost.usage_date <= end_date,
            ).all()
        }

        incoming_dates = set()
        for item in snapshot.get('rows') or []:
            usage_date = date.fromisoformat(item['day'])
            incoming_dates.add(usage_date)

            row = existing_rows.get(usage_date)
            if row is None:
                row = FinOpsDailyCost(
                    subscription_id=subscription_id,
                    usage_date=usage_date,
                )
                db.session.add(row)

            row.currency_code = snapshot.get('currency_code') or 'USD'
            row.total_cost = item.get('total_cost', 0)
            row.source_system = 'azure_cost_management'
            row.last_synced_at = now

        for usage_date, row in existing_rows.items():
            if usage_date not in incoming_dates:
                db.session.delete(row)

        sync_state = _ensure_sync_state(subscription_id, year, month)
        sync_state.last_attempted_at = now
        sync_state.last_synced_at = now
        sync_state.last_error = None

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _record_sync_failure(subscription_id, year, month, exc)
        current_app.logger.exception(
            'Failed to sync FinOps daily costs for %s %04d-%02d.',
            subscription_id,
            year,
            month,
        )
        raise

    warm_daily_cost_cache(subscription_id, year, month)

    current_month = _utcnow()
    if year == current_month.year and month == current_month.month:
        try:
            from services.alerts_service import sync_finops_daily_cost_threshold_alerts

            sync_finops_daily_cost_threshold_alerts(subscription_id, year, month)
        except Exception:
            current_app.logger.exception(
                'Failed to refresh FinOps daily threshold alerts for %04d-%02d.',
                year,
                month,
            )

    next_year, next_month = _next_month(year, month)
    _clear_daily_cost_cache(next_year, next_month)
    return True


def _run_async_refresh(app, refresh_key: str, task):
    try:
        with app.app_context():
            task()
    except Exception:
        app.logger.exception('Background FinOps refresh failed for %s.', refresh_key)
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
        name=f'finops-refresh-{refresh_key}',
    )
    thread.start()
    return True


def schedule_daily_cost_sync_if_due(
    subscription_id: str,
    year: int,
    month: int,
):
    year = int(year)
    month = int(month)

    state = _daily_sync_state(subscription_id, year, month)
    if not _sync_is_due(state):
        return False

    refresh_key = _sync_state_key(subscription_id, year, month)
    return _start_async_refresh(
        refresh_key,
        lambda: sync_finops_daily_cost_month(
            subscription_id,
            year,
            month,
            force=True,
        ),
    )


def _schedule_daily_cost_refreshes_if_due(
    subscription_id: str,
    year: int,
    month: int,
    *,
    current_state: Optional[FinOpsSyncState] = None,
    previous_state: Optional[FinOpsSyncState] = None,
):
    prev_year, prev_month = _previous_month(year, month)

    if _state_needs_sync(current_state):
        schedule_daily_cost_sync_if_due(subscription_id, year, month)

    if _state_needs_sync(previous_state):
        schedule_daily_cost_sync_if_due(subscription_id, prev_year, prev_month)


def get_finops_daily_cost_chart(
    subscription_id: str,
    year: int,
    month: int,
    *,
    service: Optional[FinOpsService] = None,
    serve_stored_first: bool = False,
):
    year = int(year)
    month = int(month)

    stored = get_cached_stored_daily_cost_chart(subscription_id, year, month)
    prev_year, prev_month = _previous_month(year, month)
    current_state = _daily_sync_state(subscription_id, year, month)
    previous_state = _daily_sync_state(subscription_id, prev_year, prev_month)

    current_month_is_ready = (
        stored is not None
        and current_state is not None
        and current_state.last_synced_at is not None
        and not _sync_is_due(current_state)
    )

    if serve_stored_first and stored is not None:
        _schedule_daily_cost_refreshes_if_due(
            subscription_id,
            year,
            month,
            current_state=current_state,
            previous_state=previous_state,
        )
        return stored

    if current_month_is_ready:
        schedule_daily_cost_sync_if_due(subscription_id, prev_year, prev_month)
        return stored

    live_service = service or _build_live_service(subscription_id)
    sync_finops_daily_cost_month(
        subscription_id,
        year,
        month,
        service=live_service,
        force=True,
    )
    try:
        if _state_needs_sync(previous_state):
            sync_finops_daily_cost_month(
                subscription_id,
                prev_year,
                prev_month,
                service=live_service,
                force=True,
            )
    except Exception:
        current_app.logger.exception(
            'FinOps previous-month backfill failed for %04d-%02d.',
            prev_year,
            prev_month,
        )

    stored = get_cached_stored_daily_cost_chart(subscription_id, year, month)
    if stored is not None:
        return stored

    raise RuntimeError('No FinOps daily cost data is available yet for the requested month.')


def refresh_finops_month(
    subscription_id: str,
    year: int,
    month: int,
    *,
    force: bool = True,
):
    year = int(year)
    month = int(month)

    live_service = _build_live_service(subscription_id)
    prev_year, prev_month = _previous_month(year, month)

    sync_finops_daily_cost_month(
        subscription_id,
        prev_year,
        prev_month,
        service=live_service,
        force=force,
    )
    changed = sync_finops_daily_cost_month(
        subscription_id,
        year,
        month,
        service=live_service,
        force=force,
    )

    return {
        'daily_costs': {
            'actual': {
                'synced': True,
                'changed': bool(changed),
            }
        }
    }
