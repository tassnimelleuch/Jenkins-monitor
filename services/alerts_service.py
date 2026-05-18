from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from alerts_models import PersistentAlert
from config import Config
from extensions import db
from finops_models import FinOpsDailyCost
from services.finops_storage_service import get_finops_daily_cost_chart
from services.pipeline_storage_service import get_stored_pipeline_kpis


ALERT_RULE_ID = 'build_duration_over_one_minute'
TEST_ALERT_THRESHOLD_MS = 60_000
FINOPS_DAILY_COST_RULE_ID = 'finops_daily_cost_above_average'
FINOPS_TOTAL_SCOPE = 'total'
FINOPS_TOTAL_LABEL = 'Total cost'


def _utcnow():
    return datetime.now(timezone.utc)


def _selected_branch_payload(payload):
    pipeline = payload.get('pipeline') or {}
    branches = payload.get('branches') or {}
    selected_branch = pipeline.get('selected_branch')
    return pipeline, selected_branch, (branches.get(selected_branch) or {})


def _build_duration_ms(build, now_ms):
    duration_ms = build.get('duration_ms')
    if duration_ms is None:
        duration_ms = build.get('duration')
    if duration_ms is None:
        duration_ms = int(build.get('duration_seconds') or 0) * 1000

    duration_ms = int(duration_ms or 0)
    if build.get('result') is not None:
        return duration_ms

    started_at_ms = int(build.get('timestamp') or 0)
    if started_at_ms:
        duration_ms = max(duration_ms, max(now_ms - started_at_ms, 0))
    return duration_ms


def _current_month_bounds(year: int, month: int):
    last_day = monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, last_day)
    today = _utcnow().date()
    if year == today.year and month == today.month:
        end = min(end, today)
    return start, end


def _to_float(value) -> float:
    return round(float(value or 0.0), 4)


def _timestamp_ms(value):
    if value is None:
        return None

    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return int(normalized.timestamp() * 1000)


def _finops_alert_key(subscription_id: str, usage_date: date) -> str:
    return (
        f'{FINOPS_DAILY_COST_RULE_ID}:{subscription_id}:'
        f'{usage_date.isoformat()}:{FINOPS_TOTAL_SCOPE}'
    )


def _upsert_finops_alert(
    subscription_id: str,
    row,
    current_value: float,
    average_value: float,
):
    alert_key = _finops_alert_key(subscription_id, row.usage_date)
    existing = PersistentAlert.query.filter_by(alert_key=alert_key).one_or_none()
    if existing is not None and existing.is_checked:
        return None

    now = _utcnow()
    delta_value = round(current_value - average_value, 4)
    message = (
        f'{FINOPS_TOTAL_LABEL} on {row.usage_date.isoformat()} '
        f'exceeded the month-to-date daily average.'
    )

    if existing is None:
        existing = PersistentAlert(
            alert_key=alert_key,
            rule_id=FINOPS_DAILY_COST_RULE_ID,
            source_system='finops',
            severity='warning',
            title='Total daily cost spike',
            resource_scope=FINOPS_TOTAL_SCOPE,
            year=row.usage_date.year,
            month=row.usage_date.month,
            usage_date=row.usage_date,
            first_detected_at=now,
        )
        db.session.add(existing)
    else:
        existing_payload = existing.payload or {}
        incoming_payload = {
            'subscription_id': subscription_id,
            'scope_name': FINOPS_TOTAL_SCOPE,
            'scope_label': FINOPS_TOTAL_LABEL,
            'usage_date': row.usage_date.isoformat(),
            'month_label': f'{row.usage_date.year}-{row.usage_date.month:02d}',
        }
        if (
            existing.message == message
            and (existing.currency_code or 'USD') == (row.currency_code or 'USD')
            and _to_float(existing.current_value) == current_value
            and _to_float(existing.threshold_value) == average_value
            and _to_float(existing.delta_value) == delta_value
            and existing_payload == incoming_payload
        ):
            return None

    existing.message = message
    existing.currency_code = row.currency_code or 'USD'
    existing.current_value = current_value
    existing.threshold_value = average_value
    existing.delta_value = delta_value
    existing.payload = {
        'subscription_id': subscription_id,
        'scope_name': FINOPS_TOTAL_SCOPE,
        'scope_label': FINOPS_TOTAL_LABEL,
        'usage_date': row.usage_date.isoformat(),
        'month_label': f'{row.usage_date.year}-{row.usage_date.month:02d}',
    }
    existing.last_detected_at = now
    return existing


def _delete_legacy_finops_alerts(subscription_id: str):
    legacy_rows = (
        PersistentAlert.query
        .filter(
            PersistentAlert.rule_id == FINOPS_DAILY_COST_RULE_ID,
            PersistentAlert.is_checked.is_(False),
            PersistentAlert.resource_scope != FINOPS_TOTAL_SCOPE,
        )
        .all()
    )
    removed = 0
    for row in legacy_rows:
        payload = row.payload or {}
        if payload.get('subscription_id') and payload.get('subscription_id') != subscription_id:
            continue
        db.session.delete(row)
        removed += 1
    return removed


def sync_finops_daily_cost_threshold_alerts(
    subscription_id: str,
    year: int | None = None,
    month: int | None = None,
):
    if not subscription_id:
        return []

    now = _utcnow()
    target_year = int(year or now.year)
    target_month = int(month or now.month)

    # Ensure the current month of FinOps data exists, and let the existing
    # storage service schedule a background refresh if it is stale.
    get_finops_daily_cost_chart(
        subscription_id,
        target_year,
        target_month,
    )

    start_date, end_date = _current_month_bounds(target_year, target_month)
    rows = (
        FinOpsDailyCost.query
        .filter(
            FinOpsDailyCost.subscription_id == subscription_id,
            FinOpsDailyCost.usage_date >= start_date,
            FinOpsDailyCost.usage_date <= end_date,
        )
        .order_by(FinOpsDailyCost.usage_date.asc())
        .all()
    )
    if not rows:
        _delete_legacy_finops_alerts(subscription_id)
        db.session.commit()
        return []

    count = len(rows)
    average_total = round(
        sum(_to_float(row.total_cost) for row in rows) / count,
        4,
    )

    changed = bool(_delete_legacy_finops_alerts(subscription_id))
    for row in rows:
        current_value = _to_float(row.total_cost)
        if average_total <= 0 or current_value <= average_total:
            continue

        alert_row = _upsert_finops_alert(
            subscription_id,
            row,
            current_value,
            average_total,
        )
        changed = changed or alert_row is not None

    if changed:
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception(
                'Failed to persist FinOps daily threshold alerts for %04d-%02d.',
                target_year,
                target_month,
            )
            return []

    return rows


def mark_persistent_alert_checked(alert_id: int, checked_by_username: str | None = None):
    row = db.session.get(PersistentAlert, int(alert_id))
    if row is None:
        return None

    if row.is_checked:
        return row

    row.is_checked = True
    row.checked_at = _utcnow()
    row.checked_by_username = (checked_by_username or '').strip() or None

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to mark persistent alert %s as checked.',
            alert_id,
        )
        raise

    return row


def _serialize_persistent_alert(row):
    payload = row.payload or {}
    return {
        'id': row.id,
        'kind': 'finops_daily_cost',
        'rule_id': row.rule_id,
        'source_label': 'FinOps',
        'label': FINOPS_TOTAL_LABEL,
        'severity': row.severity,
        'message': row.message,
        'usage_date': row.usage_date.isoformat() if row.usage_date else None,
        'month_label': payload.get('month_label') or f'{row.year}-{row.month:02d}',
        'currency_code': row.currency_code or 'USD',
        'current_value': _to_float(row.current_value),
        'threshold_value': _to_float(row.threshold_value),
        'delta_value': _to_float(row.delta_value),
        'first_detected_at': _timestamp_ms(row.first_detected_at),
        'last_detected_at': _timestamp_ms(row.last_detected_at),
        'requires_check': True,
        'persistent': True,
    }


def _duration_alerts_payload():
    payload = get_stored_pipeline_kpis() or {}
    if not payload:
        return {
            'pipeline': {
                'name': 'Jenkins Pipeline',
                'selected_branch': None,
            },
            'summary': {
                'alert_count': 0,
                'running_builds': 0,
                'avg_duration_ms': 0,
                'threshold_ms': TEST_ALERT_THRESHOLD_MS,
            },
            'alerts': [],
        }

    pipeline, selected_branch, branch_payload = _selected_branch_payload(payload)
    summary = branch_payload.get('summary') or {}
    builds = branch_payload.get('builds') or []
    avg_duration_ms = int(summary.get('avg_duration_ms') or 0)
    now_ms = int(_utcnow().timestamp() * 1000)

    alerts = []
    running_builds = 0
    for build in builds:
        if build.get('result') is not None:
            continue

        running_builds += 1
        duration_ms = _build_duration_ms(build, now_ms)
        if duration_ms <= TEST_ALERT_THRESHOLD_MS:
            continue

        build_number = build.get('number')
        exceeded_by_ms = duration_ms - TEST_ALERT_THRESHOLD_MS
        alerts.append({
            'id': f'{ALERT_RULE_ID}:{build_number}',
            'kind': 'build_duration',
            'rule_id': ALERT_RULE_ID,
            'source_label': 'Jenkins',
            'label': f'Build #{build_number}',
            'severity': 'warning',
            'build_number': build_number,
            'duration_ms': duration_ms,
            'threshold_ms': TEST_ALERT_THRESHOLD_MS,
            'avg_duration_ms': avg_duration_ms,
            'exceeded_by_ms': exceeded_by_ms,
            'started_at': build.get('timestamp'),
            'message': (
                f'Build #{build_number} has been running longer than 1 minute.'
            ),
            'requires_check': False,
            'persistent': False,
        })

    alerts.sort(key=lambda item: (item.get('exceeded_by_ms') or 0), reverse=True)

    return {
        'pipeline': {
            'name': pipeline.get('name') or 'Jenkins Pipeline',
            'selected_branch': selected_branch,
        },
        'summary': {
            'alert_count': len(alerts),
            'running_builds': running_builds,
            'avg_duration_ms': avg_duration_ms,
            'threshold_ms': TEST_ALERT_THRESHOLD_MS,
        },
        'alerts': alerts,
    }


def get_alerts_payload():
    duration_payload = _duration_alerts_payload()
    finops_alerts = []

    subscription_id = Config.AZURE_SUBSCRIPTION_ID
    if subscription_id:
        now = _utcnow()
        try:
            sync_finops_daily_cost_threshold_alerts(subscription_id, now.year, now.month)
        except Exception:
            current_app.logger.exception(
                'Failed to sync FinOps threshold alerts for the alerts page.'
            )

        finops_rows = (
            PersistentAlert.query
            .filter(
                PersistentAlert.rule_id == FINOPS_DAILY_COST_RULE_ID,
                PersistentAlert.is_checked.is_(False),
                PersistentAlert.resource_scope == FINOPS_TOTAL_SCOPE,
            )
            .order_by(
                PersistentAlert.last_detected_at.desc(),
                PersistentAlert.created_at.desc(),
            )
            .all()
        )
        finops_alerts = [_serialize_persistent_alert(row) for row in finops_rows]

    alerts = finops_alerts + (duration_payload.get('alerts') or [])
    generated_at = int(_utcnow().timestamp() * 1000)

    return {
        'connected': True,
        'pipeline': duration_payload.get('pipeline') or {},
        'summary': {
            'alert_count': len(alerts),
            'finops_alert_count': len(finops_alerts),
            'build_alert_count': len(duration_payload.get('alerts') or []),
            'running_builds': (duration_payload.get('summary') or {}).get('running_builds', 0),
            'avg_duration_ms': (duration_payload.get('summary') or {}).get('avg_duration_ms', 0),
            'threshold_ms': (duration_payload.get('summary') or {}).get('threshold_ms', TEST_ALERT_THRESHOLD_MS),
        },
        'alerts': alerts,
        'generated_at': generated_at,
    }
