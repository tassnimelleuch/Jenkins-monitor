from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from alerts_models import PersistentAlert
from collectors.github_collector import get_commit, get_pull_requests
from collectors.jenkins_collector import (
    extract_build_commit_sha,
    extract_build_commits,
    extract_build_culprits,
    get_build_info,
)
from collectors.prometheus_collector import query
from config import Config
from extensions import cache, db
from finops_models import FinOpsDailyCost
from services.finops_storage_service import get_finops_daily_cost_chart
from services.jenkins_service import (
    get_live_running_builds,
    refresh_pipeline_storage_from_jenkins,
)
from services.pipeline_storage_service import get_stored_pipeline_kpis
from services.prometheus_queries import (
    CLUSTER_NODE_CPU_PCT_QUERY,
    CLUSTER_NODE_RAM_PCT_QUERY,
    CLUSTER_POD_CPU_PCT_QUERY,
    CLUSTER_POD_RAM_LIMIT_BYTES_QUERY,
    CLUSTER_POD_RAM_USED_BYTES_QUERY,
    VM_CPU_PCT_QUERY,
    VM_DISK_USED_PCT_QUERY,
    VM_RAM_PCT_QUERY,
)


FINOPS_DAILY_COST_RULE_ID = 'finops_daily_cost_above_average'
FINOPS_TOTAL_SCOPE = 'total'
FINOPS_TOTAL_LABEL = 'Total cost'
PULL_REQUEST_STALE_RULE_ID = 'open_pull_request_over_three_days'
MS_PER_DAY = 24 * 60 * 60 * 1000
PULL_REQUEST_STALE_THRESHOLD_MS = 3 * MS_PER_DAY
BUILD_FAILURE_STREAK_RULE_ID = 'consecutive_build_failures'
BUILD_FAILURE_STREAK_THRESHOLD = 2
STAGE_DURATION_OVER_AVERAGE_RULE_ID = 'stage_duration_over_average'
GITHUB_PR_ALERTS_CACHE_VERSION = 'v1'
GITHUB_PR_ALERTS_CACHE_TIMEOUT_SECONDS = 60
FAILED_BUILD_AUTHOR_CACHE_VERSION = 'v1'
FAILED_BUILD_AUTHOR_CACHE_TIMEOUT_SECONDS = 300
PROMETHEUS_ALERTS_CACHE_VERSION = 'v1'
PROMETHEUS_ALERTS_CACHE_TIMEOUT_SECONDS = 60
PROMETHEUS_THRESHOLD_ALERT_RULE_ID = 'prometheus_metric_over_ninety_percent'
PROMETHEUS_THRESHOLD_PERCENT = 90.0
TERMINAL_STAGE_STATUSES = {'SUCCESS', 'FAILED', 'ABORTED', 'NOT_EXECUTED'}

ALERT_KIND_BY_RULE_ID = {
    FINOPS_DAILY_COST_RULE_ID: 'finops_daily_cost',
    PULL_REQUEST_STALE_RULE_ID: 'open_pull_request_age',
    BUILD_FAILURE_STREAK_RULE_ID: 'build_failure_streak',
    STAGE_DURATION_OVER_AVERAGE_RULE_ID: 'stage_duration_over_average',
    PROMETHEUS_THRESHOLD_ALERT_RULE_ID: 'prometheus_metric_threshold',
}

SOURCE_LABEL_BY_SYSTEM = {
    'finops': 'FinOps',
    'github': 'GitHub',
    'jenkins': 'Jenkins',
    'prometheus': 'Prometheus',
}

TITLE_BY_KIND = {
    'finops_daily_cost': 'Total daily cost spike',
    'open_pull_request_age': 'Stale pull request',
    'build_failure_streak': 'Build failure streak',
    'stage_duration_over_average': 'Slow running stage',
    'prometheus_metric_threshold': 'Prometheus threshold breach',
}

LABEL_BY_KIND = {
    'finops_daily_cost': FINOPS_TOTAL_LABEL,
    'open_pull_request_age': 'Pull request',
    'build_failure_streak': 'Pipeline',
    'stage_duration_over_average': 'Stage duration',
    'prometheus_metric_threshold': 'Metric',
}

PERSISTED_PAYLOAD_RESERVED_KEYS = {
    'id',
    'alert_key',
    'rule_id',
    'source_system',
    'severity',
    'message',
    'title',
    'resource_scope',
    'current_value',
    'threshold_value',
    'delta_value',
    'requires_check',
    'persistent',
    'is_checked',
    'checked_at',
    'checked_by',
    'first_detected_at',
    'last_detected_at',
}


def _utcnow():
    return datetime.now(timezone.utc)


def _selected_branch_payload(payload):
    pipeline = payload.get('pipeline') or {}
    branches = payload.get('branches') or {}
    selected_branch = pipeline.get('selected_branch')
    return pipeline, selected_branch, (branches.get(selected_branch) or {})


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


def _parse_iso_datetime(value):
    if not value or not isinstance(value, str):
        return None

    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_usage_date(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            parsed = _parse_iso_datetime(value)
            return parsed.date() if parsed is not None else None
    return None


def _normalize_person_name(name):
    return ' '.join(str(name or '').split()).casefold()


def _normalize_stage_name(name):
    return ' '.join(str(name or '').split()).casefold()


def _main_pipeline_context():
    payload = get_stored_pipeline_kpis() or {}
    pipeline, selected_branch, branch_payload = _selected_branch_payload(payload)
    branch_name = selected_branch or 'main'
    return {
        'pipeline': {
            'name': pipeline.get('name') or 'Jenkins Pipeline',
            'selected_branch': branch_name,
        },
        'branch_name': branch_name,
        'builds': branch_payload.get('builds') or [],
    }


def _completed_builds(builds):
    return [build for build in (builds or []) if build.get('result') is not None]


def _current_stage_duration_ms(stage, now_ms):
    duration_ms = int(stage.get('duration_ms') or 0)
    started_at_ms = int(stage.get('start_time') or 0)
    if started_at_ms > 0:
        duration_ms = max(duration_ms, max(now_ms - started_at_ms, 0))
    return duration_ms


def _stage_is_active(stage):
    status = (stage.get('status') or '').strip().upper()
    if status in TERMINAL_STAGE_STATUSES:
        return False
    if not status:
        return False
    return bool(int(stage.get('start_time') or 0) > 0 or int(stage.get('duration_ms') or 0) > 0)


def _historical_stage_average_durations_ms(builds):
    totals = {}
    counts = {}

    for build in _completed_builds(builds):
        for stage in build.get('stages') or []:
            stage_name = (stage.get('name') or '').strip()
            duration_ms = int(stage.get('duration_ms') or 0)
            if not stage_name or duration_ms <= 0:
                continue

            normalized_stage_name = _normalize_stage_name(stage_name)
            if not normalized_stage_name:
                continue

            totals[normalized_stage_name] = totals.get(normalized_stage_name, 0) + duration_ms
            counts[normalized_stage_name] = counts.get(normalized_stage_name, 0) + 1

    return {
        stage_name: int(totals[stage_name] / counts[stage_name])
        for stage_name in sorted(totals)
        if counts.get(stage_name)
    }


def _github_pr_alerts_cache_key(owner: str, repo: str, branch_name: str) -> str:
    normalized_branch = (branch_name or 'main').strip() or 'main'
    return (
        f'alerts:github_prs:{GITHUB_PR_ALERTS_CACHE_VERSION}:'
        f'{owner}:{repo}:{normalized_branch}'
    )


def _failed_build_author_cache_key(owner: str, repo: str, branch_name: str, build_number: int) -> str:
    normalized_branch = (branch_name or 'main').strip() or 'main'
    return (
        f'alerts:failed_build_author:{FAILED_BUILD_AUTHOR_CACHE_VERSION}:'
        f'{owner}:{repo}:{normalized_branch}:{int(build_number)}'
    )


def _prometheus_alerts_cache_key() -> str:
    return f'alerts:prometheus:{PROMETHEUS_ALERTS_CACHE_VERSION}'


def _prometheus_scalar_alert_definitions():
    return (
        ('vm_cpu_pct', 'Jenkins VM CPU', VM_CPU_PCT_QUERY),
        ('vm_ram_pct', 'Jenkins VM RAM', VM_RAM_PCT_QUERY),
        ('vm_disk_pct', 'Jenkins VM Disk', VM_DISK_USED_PCT_QUERY),
        ('cluster_node_cpu_pct', 'AKS Node CPU', CLUSTER_NODE_CPU_PCT_QUERY),
        ('cluster_node_ram_pct', 'AKS Node RAM', CLUSTER_NODE_RAM_PCT_QUERY),
        ('cluster_pod_cpu_pct', 'AKS Pod CPU', CLUSTER_POD_CPU_PCT_QUERY),
    )


def _finops_alert_key(subscription_id: str, usage_date: date) -> str:
    return (
        f'{FINOPS_DAILY_COST_RULE_ID}:{subscription_id}:'
        f'{usage_date.isoformat()}:{FINOPS_TOTAL_SCOPE}'
    )


def _kind_from_rule_id(rule_id: str | None):
    return ALERT_KIND_BY_RULE_ID.get((rule_id or '').strip())


def _source_label_for_system(source_system: str | None):
    return SOURCE_LABEL_BY_SYSTEM.get((source_system or '').strip(), 'Alert')


def _title_for_alert(alert):
    title = str(alert.get('title') or '').strip()
    if title:
        return title[:255]

    kind = str(alert.get('kind') or '').strip()
    fallback = TITLE_BY_KIND.get(kind)
    if fallback:
        return fallback[:255]

    label = str(alert.get('label') or '').strip()
    return (label or 'Alert')[:255]


def _resource_scope_for_alert(alert):
    resource_scope = str(alert.get('resource_scope') or '').strip()
    if resource_scope:
        return resource_scope[:32]

    kind = str(alert.get('kind') or '').strip()
    if kind == 'finops_daily_cost':
        return FINOPS_TOTAL_SCOPE
    if kind == 'open_pull_request_age':
        return 'pull_request'
    if kind == 'build_failure_streak':
        return 'pipeline'
    if kind == 'stage_duration_over_average':
        return 'stage'
    if kind == 'prometheus_metric_threshold':
        return 'metric'
    return 'generic'


def _value_triplet_for_alert(alert):
    if alert.get('current_value') is not None or alert.get('threshold_value') is not None:
        return (
            _to_float(alert.get('current_value')),
            _to_float(alert.get('threshold_value')),
            _to_float(alert.get('delta_value')),
        )

    kind = str(alert.get('kind') or '').strip()
    if kind == 'open_pull_request_age':
        return (
            _to_float(alert.get('age_ms')),
            _to_float(alert.get('threshold_ms')),
            _to_float(alert.get('exceeded_by_ms')),
        )
    if kind == 'build_failure_streak':
        streak_count = int(alert.get('streak_count') or 0)
        threshold_count = int(alert.get('threshold_count') or 0)
        return (
            _to_float(streak_count),
            _to_float(threshold_count),
            _to_float(max(streak_count - threshold_count, 0)),
        )
    if kind == 'stage_duration_over_average':
        return (
            _to_float(alert.get('duration_ms')),
            _to_float(alert.get('threshold_ms')),
            _to_float(alert.get('exceeded_by_ms')),
        )
    return (0.0, 0.0, 0.0)


def _serialize_alert_payload(alert, usage_date_value):
    payload = {}
    for key, value in (alert or {}).items():
        if key in PERSISTED_PAYLOAD_RESERVED_KEYS:
            continue
        payload[key] = value

    if usage_date_value is not None:
        payload['usage_date'] = usage_date_value.isoformat()
    return payload


def _persistent_alert_input(alert):
    alert_key = str(alert.get('alert_key') or alert.get('id') or '').strip()
    if not alert_key:
        return None

    usage_date_value = _normalize_usage_date(alert.get('usage_date'))
    timestamp_reference = usage_date_value or _utcnow().date()
    current_value, threshold_value, delta_value = _value_triplet_for_alert(alert)
    return {
        'alert_key': alert_key,
        'rule_id': str(alert.get('rule_id') or '').strip() or 'generic',
        'source_system': str(alert.get('source_system') or '').strip() or 'generic',
        'severity': str(alert.get('severity') or 'warning').strip() or 'warning',
        'title': _title_for_alert(alert),
        'message': str(alert.get('message') or 'Alert triggered.').strip() or 'Alert triggered.',
        'resource_scope': _resource_scope_for_alert(alert),
        'year': int(timestamp_reference.year),
        'month': int(timestamp_reference.month),
        'usage_date': usage_date_value,
        'currency_code': str(alert.get('currency_code') or 'USD').strip() or 'USD',
        'current_value': current_value,
        'threshold_value': threshold_value,
        'delta_value': delta_value,
        'payload': _serialize_alert_payload(alert, usage_date_value),
    }


def _upsert_persistent_alert_row(existing, alert):
    prepared = _persistent_alert_input(alert)
    if prepared is None:
        return None, False

    now = _utcnow()
    changed = False
    row = existing
    if row is None:
        row = PersistentAlert(
            alert_key=prepared['alert_key'],
            first_detected_at=now,
            last_detected_at=now,
        )
        db.session.add(row)
        changed = True

    for attr in (
        'rule_id',
        'source_system',
        'severity',
        'title',
        'message',
        'resource_scope',
        'year',
        'month',
        'usage_date',
        'currency_code',
        'payload',
    ):
        value = prepared[attr]
        if getattr(row, attr) != value:
            setattr(row, attr, value)
            changed = True

    for attr in ('current_value', 'threshold_value', 'delta_value'):
        value = prepared[attr]
        if _to_float(getattr(row, attr)) != value:
            setattr(row, attr, value)
            changed = True

    row.last_detected_at = now
    changed = True
    return row, changed


def _sync_persistent_alerts(alerts):
    cleaned_alerts = [alert for alert in (alerts or []) if alert]
    if not cleaned_alerts:
        return False

    alert_keys = [
        str(alert.get('alert_key') or alert.get('id') or '').strip()
        for alert in cleaned_alerts
        if str(alert.get('alert_key') or alert.get('id') or '').strip()
    ]
    existing_rows = {}
    if alert_keys:
        existing_rows = {
            row.alert_key: row
            for row in (
                PersistentAlert.query
                .filter(PersistentAlert.alert_key.in_(alert_keys))
                .all()
            )
        }

    changed = False
    for alert in cleaned_alerts:
        alert_key = str(alert.get('alert_key') or alert.get('id') or '').strip()
        if not alert_key:
            continue
        row, row_changed = _upsert_persistent_alert_row(existing_rows.get(alert_key), alert)
        if row is not None:
            existing_rows[alert_key] = row
        changed = changed or row_changed

    if not changed:
        return False

    try:
        db.session.commit()
        return True
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to persist alert rows.')
        return False


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


def _build_finops_detected_alerts(subscription_id: str, rows):
    if not rows:
        return []

    count = len(rows)
    average_total = round(
        sum(_to_float(row.total_cost) for row in rows) / count,
        4,
    )
    if average_total <= 0:
        return []

    alerts = []
    for row in rows:
        current_value = _to_float(row.total_cost)
        if current_value <= average_total:
            continue

        delta_value = round(current_value - average_total, 4)
        alerts.append({
            'alert_key': _finops_alert_key(subscription_id, row.usage_date),
            'kind': 'finops_daily_cost',
            'rule_id': FINOPS_DAILY_COST_RULE_ID,
            'source_system': 'finops',
            'source_label': 'FinOps',
            'label': FINOPS_TOTAL_LABEL,
            'title': 'Daily cost above average',
            'severity': 'warning',
            'resource_scope': FINOPS_TOTAL_SCOPE,
            'usage_date': row.usage_date,
            'month_label': f'{row.usage_date.year}-{row.usage_date.month:02d}',
            'currency_code': row.currency_code or 'USD',
            'current_value': current_value,
            'threshold_value': average_total,
            'delta_value': delta_value,
            'subscription_id': subscription_id,
            'scope_name': FINOPS_TOTAL_SCOPE,
            'scope_label': FINOPS_TOTAL_LABEL,
            'message': 'Daily total cost is above the average for this month.',
        })
    return alerts


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

    changed = bool(_delete_legacy_finops_alerts(subscription_id))
    if not rows:
        if changed:
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception(
                    'Failed to remove legacy FinOps alerts for %04d-%02d.',
                    target_year,
                    target_month,
                )
        return []

    finops_alerts = _build_finops_detected_alerts(subscription_id, rows)
    changed = _sync_persistent_alerts(finops_alerts) or changed
    if changed and not finops_alerts:
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception(
                'Failed to persist FinOps alert cleanup for %04d-%02d.',
                target_year,
                target_month,
            )
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


def _persistent_alert_defaults(row, payload):
    kind = payload.get('kind') or _kind_from_rule_id(row.rule_id) or 'generic'
    defaults = {
        'kind': kind,
        'source_label': payload.get('source_label') or _source_label_for_system(row.source_system),
        'label': payload.get('label') or LABEL_BY_KIND.get(kind) or row.title or 'Alert',
        'title': payload.get('title') or row.title,
    }
    if kind == 'finops_daily_cost':
        defaults['label'] = payload.get('label') or FINOPS_TOTAL_LABEL
        defaults['month_label'] = payload.get('month_label') or f'{row.year}-{row.month:02d}'
    return defaults


def _enrich_build_failure_streak_payload(payload):
    kind = str((payload or {}).get('kind') or '').strip()
    if kind != 'build_failure_streak':
        return payload, False

    first_failed_build_number = payload.get('first_failed_build_number')
    branch_name = str(payload.get('branch_name') or 'main').strip() or 'main'
    has_author = bool(
        str(payload.get('first_failed_author_login') or '').strip()
        or str(payload.get('first_failed_author_name') or '').strip()
    )
    has_commit = bool(str(payload.get('first_failed_commit_sha') or '').strip())

    if first_failed_build_number is None or (has_author and has_commit):
        return payload, False

    try:
        first_failed_author = _failed_build_github_author(branch_name, first_failed_build_number)
    except Exception:
        current_app.logger.exception(
            'Failed to enrich build failure streak alert details for branch %s build %s.',
            branch_name,
            first_failed_build_number,
        )
        return payload, False

    enriched = dict(payload or {})
    changed = False
    for key, value in (
        ('first_failed_author_login', first_failed_author.get('author_login')),
        ('first_failed_author_name', first_failed_author.get('author_name')),
        ('first_failed_commit_sha', first_failed_author.get('commit_sha')),
    ):
        if value and enriched.get(key) != value:
            enriched[key] = value
            changed = True

    return enriched, changed


def _enrich_persistent_alert_rows(rows):
    changed = False
    for row in rows or []:
        payload = dict(row.payload or {})
        enriched_payload, payload_changed = _enrich_build_failure_streak_payload(payload)
        if not payload_changed:
            continue
        row.payload = enriched_payload
        changed = True

    if not changed:
        return False

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to persist enriched build failure streak alert details.')
        return False
    return True


def _serialize_persistent_alert(row):
    payload = dict(row.payload or {})
    defaults = _persistent_alert_defaults(row, payload)
    usage_date_value = row.usage_date.isoformat() if row.usage_date else payload.get('usage_date')

    alert = {
        **defaults,
        **payload,
        'id': row.id,
        'alert_key': row.alert_key,
        'rule_id': row.rule_id,
        'source_system': row.source_system,
        'severity': row.severity,
        'message': row.message,
        'usage_date': usage_date_value,
        'currency_code': row.currency_code or payload.get('currency_code') or 'USD',
        'current_value': _to_float(row.current_value),
        'threshold_value': _to_float(row.threshold_value),
        'delta_value': _to_float(row.delta_value),
        'first_detected_at': _timestamp_ms(row.first_detected_at),
        'last_detected_at': _timestamp_ms(row.last_detected_at),
        'requires_check': not row.is_checked,
        'persistent': True,
        'is_checked': bool(row.is_checked),
        'checked_at': _timestamp_ms(row.checked_at),
        'checked_by': row.checked_by_username,
    }
    return alert


def _select_failed_pipeline_commit_sha(build_info, build_commits, culprits):
    culprit_names = [
        _normalize_person_name((culprit or {}).get('full_name'))
        for culprit in (culprits or [])
        if _normalize_person_name((culprit or {}).get('full_name'))
    ]
    for culprit_name in culprit_names:
        for commit in build_commits or []:
            if _normalize_person_name((commit or {}).get('author_name')) == culprit_name and commit.get('sha'):
                return commit.get('sha')
    return extract_build_commit_sha(build_info)


def _failed_build_github_author(branch_name: str, build_number: int | None):
    if build_number is None:
        return {}

    owner = (current_app.config.get('GITHUB_OWNER') or '').strip()
    repo = (current_app.config.get('GITHUB_REPO') or '').strip()
    cache_key = _failed_build_author_cache_key(owner or '-', repo or '-', branch_name, int(build_number))
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    build_info = get_build_info(build_number, branch_name=branch_name)
    build_commits = extract_build_commits(build_info)
    culprits = extract_build_culprits(build_info)
    failed_sha = _select_failed_pipeline_commit_sha(build_info, build_commits, culprits)

    author_login = None
    author_name = None
    if owner and repo and failed_sha:
        commit_raw = get_commit(owner, repo, failed_sha)
        if isinstance(commit_raw, dict):
            commit = commit_raw.get('commit', {}) or {}
            author_user = commit_raw.get('author') or {}
            commit_author = commit.get('author') or {}
            committer = commit.get('committer') or {}
            author_login = author_user.get('login')
            author_name = (
                commit_author.get('name')
                or author_user.get('login')
                or committer.get('name')
            )

    if not author_name and failed_sha:
        matching_commit = next(
            (item for item in build_commits if item.get('sha') == failed_sha),
            None,
        )
        if matching_commit:
            author_name = matching_commit.get('author_name')

    if not author_name and culprits:
        author_name = (culprits[0] or {}).get('full_name')

    payload = {
        'commit_sha': failed_sha,
        'author_login': author_login,
        'author_name': author_name,
    }
    cache.set(cache_key, payload, timeout=FAILED_BUILD_AUTHOR_CACHE_TIMEOUT_SECONDS)
    return payload


def _build_failure_streak_alerts(branch_name: str, builds):
    streak = []
    for build in _completed_builds(builds):
        result = (build.get('result') or '').strip().upper()
        if result == 'FAILURE':
            streak.append(build)
            continue
        # Only a successful build resets the streak. Other terminal outcomes
        # such as ABORTED are ignored while we scan back through history.
        if result == 'SUCCESS':
            break

    if len(streak) < BUILD_FAILURE_STREAK_THRESHOLD:
        return []

    latest_build = streak[0]
    oldest_build = streak[-1]
    first_failed_build_number = oldest_build.get('number')
    first_failed_author = _failed_build_github_author(branch_name, first_failed_build_number)
    first_failed_by = (
        first_failed_author.get('author_login')
        or first_failed_author.get('author_name')
    )
    latest_failed_build_number = latest_build.get('number')
    build_numbers = [build.get('number') for build in streak if build.get('number') is not None]
    alert_key = (
        f'{BUILD_FAILURE_STREAK_RULE_ID}:{branch_name or "main"}:'
        f'{latest_failed_build_number or first_failed_build_number or "unknown"}'
    )
    return [{
        'alert_key': alert_key,
        'kind': 'build_failure_streak',
        'rule_id': BUILD_FAILURE_STREAK_RULE_ID,
        'source_system': 'jenkins',
        'source_label': 'Jenkins',
        'label': 'Build failures',
        'title': 'Build failures',
        'severity': 'warning',
        'branch_name': branch_name or 'main',
        'streak_count': len(streak),
        'threshold_count': BUILD_FAILURE_STREAK_THRESHOLD,
        'build_numbers': build_numbers[:5],
        'latest_failed_build_number': latest_failed_build_number,
        'first_failed_build_number': first_failed_build_number,
        'latest_failed_at': latest_build.get('timestamp'),
        'first_failed_at': oldest_build.get('timestamp'),
        'first_failed_author_login': first_failed_author.get('author_login'),
        'first_failed_author_name': first_failed_author.get('author_name'),
        'first_failed_commit_sha': first_failed_author.get('commit_sha'),
        'message': (
            'Consecutive build failures detected.'
            + (
                f' First failing GitHub user: {first_failed_by}.'
                if first_failed_by
                else ''
            )
        ),
    }]


def _stage_duration_over_average_alerts(builds, running_builds=None):
    averages_by_stage = _historical_stage_average_durations_ms(builds)
    if not averages_by_stage:
        return []

    if running_builds is None:
        running_builds = get_live_running_builds(include_stages=True) or []
    if not running_builds:
        return []

    now_ms = int(_utcnow().timestamp() * 1000)
    alerts = []
    for build in running_builds:
        build_number = build.get('number')
        for stage in build.get('stages') or []:
            stage_name = (stage.get('name') or '').strip()
            if not stage_name or not _stage_is_active(stage):
                continue

            average_duration_ms = int(
                averages_by_stage.get(_normalize_stage_name(stage_name)) or 0
            )
            if average_duration_ms <= 0:
                continue

            current_duration_ms = _current_stage_duration_ms(stage, now_ms)
            if current_duration_ms <= average_duration_ms:
                continue

            alerts.append({
                'alert_key': f'{STAGE_DURATION_OVER_AVERAGE_RULE_ID}:{build_number}:{stage_name}',
                'kind': 'stage_duration_over_average',
                'rule_id': STAGE_DURATION_OVER_AVERAGE_RULE_ID,
                'source_system': 'jenkins',
                'source_label': 'Jenkins',
                'label': 'Stage duration',
                'title': 'Slow running stage',
                'severity': 'warning',
                'stage_name': stage_name,
                'duration_ms': current_duration_ms,
                'threshold_ms': average_duration_ms,
                'exceeded_by_ms': current_duration_ms - average_duration_ms,
                'started_at': stage.get('start_time'),
                'message': (
                    f'Stage duration for "{stage_name}" '
                    f'is above its usual runtime.'
                ),
            })

    alerts.sort(key=lambda item: (item.get('exceeded_by_ms') or 0), reverse=True)
    return alerts


def _prometheus_threshold_alerts():
    cache_key = _prometheus_alerts_cache_key()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    alerts = []
    for metric_key, resource_label, promql in _prometheus_scalar_alert_definitions():
        current_value = query(promql)
        if current_value is None or current_value <= PROMETHEUS_THRESHOLD_PERCENT:
            continue

        alerts.append({
            'alert_key': f'{PROMETHEUS_THRESHOLD_ALERT_RULE_ID}:{metric_key}',
            'kind': 'prometheus_metric_threshold',
            'rule_id': PROMETHEUS_THRESHOLD_ALERT_RULE_ID,
            'source_system': 'prometheus',
            'source_label': 'Prometheus',
            'label': resource_label,
            'title': resource_label,
            'severity': 'warning',
            'metric_key': metric_key,
            'current_value': round(float(current_value), 1),
            'threshold_value': PROMETHEUS_THRESHOLD_PERCENT,
            'delta_value': round(float(current_value) - PROMETHEUS_THRESHOLD_PERCENT, 1),
            'message': (
                f'{resource_label} is above {PROMETHEUS_THRESHOLD_PERCENT:.0f}% '
                f'from the current Prometheus snapshot.'
            ),
        })

    pod_ram_used_bytes = query(CLUSTER_POD_RAM_USED_BYTES_QUERY)
    pod_ram_limit_bytes = query(CLUSTER_POD_RAM_LIMIT_BYTES_QUERY)
    if (
        pod_ram_used_bytes is not None
        and pod_ram_limit_bytes is not None
        and float(pod_ram_limit_bytes) > 0
    ):
        pod_ram_pct = (float(pod_ram_used_bytes) / float(pod_ram_limit_bytes)) * 100
        if pod_ram_pct > PROMETHEUS_THRESHOLD_PERCENT:
            alerts.append({
                'alert_key': f'{PROMETHEUS_THRESHOLD_ALERT_RULE_ID}:cluster_pod_ram_pct',
                'kind': 'prometheus_metric_threshold',
                'rule_id': PROMETHEUS_THRESHOLD_ALERT_RULE_ID,
                'source_system': 'prometheus',
                'source_label': 'Prometheus',
                'label': 'AKS Pod RAM',
                'title': 'AKS Pod RAM',
                'severity': 'warning',
                'metric_key': 'cluster_pod_ram_pct',
                'current_value': round(pod_ram_pct, 1),
                'threshold_value': PROMETHEUS_THRESHOLD_PERCENT,
                'delta_value': round(pod_ram_pct - PROMETHEUS_THRESHOLD_PERCENT, 1),
                'message': (
                    f'AKS Pod RAM is above {PROMETHEUS_THRESHOLD_PERCENT:.0f}% '
                    f'of configured memory limits from the current Prometheus snapshot.'
                ),
            })

    alerts.sort(key=lambda item: (item.get('delta_value') or 0), reverse=True)
    cache.set(cache_key, alerts, timeout=PROMETHEUS_ALERTS_CACHE_TIMEOUT_SECONDS)
    return alerts


def _stale_open_pull_request_alerts(branch_name: str):
    owner = (current_app.config.get('GITHUB_OWNER') or '').strip()
    repo = (current_app.config.get('GITHUB_REPO') or '').strip()
    if not owner or not repo:
        return []

    cache_key = _github_pr_alerts_cache_key(owner, repo, branch_name)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    now = _utcnow()
    alerts = []
    for pr in get_pull_requests(owner, repo, state='open', per_page=100) or []:
        base_branch = ((pr.get('base') or {}).get('ref') or '').strip()
        if base_branch and base_branch != (branch_name or 'main'):
            continue

        pr_number = pr.get('number')
        created_at = _parse_iso_datetime(pr.get('created_at'))
        if pr_number is None or created_at is None:
            continue

        age_ms = max(int((now - created_at).total_seconds() * 1000), 0)
        if age_ms <= PULL_REQUEST_STALE_THRESHOLD_MS:
            continue

        updated_at = _parse_iso_datetime(pr.get('updated_at'))
        user = pr.get('user') or {}
        alerts.append({
            'alert_key': f'{PULL_REQUEST_STALE_RULE_ID}:{pr_number}',
            'kind': 'open_pull_request_age',
            'rule_id': PULL_REQUEST_STALE_RULE_ID,
            'source_system': 'github',
            'source_label': 'GitHub',
            'label': f'PR #{pr_number}',
            'title': pr.get('title') or f'PR #{pr_number}',
            'severity': 'warning',
            'pr_number': pr_number,
            'author_login': user.get('login'),
            'base_branch': base_branch or (branch_name or 'main'),
            'age_ms': age_ms,
            'threshold_ms': PULL_REQUEST_STALE_THRESHOLD_MS,
            'exceeded_by_ms': age_ms - PULL_REQUEST_STALE_THRESHOLD_MS,
            'created_at': _timestamp_ms(created_at),
            'updated_at': _timestamp_ms(updated_at),
            'message': (
                f'PR #{pr_number} has been open longer than 3 days.'
            ),
        })

    alerts.sort(key=lambda item: (item.get('age_ms') or 0), reverse=True)
    cache.set(cache_key, alerts, timeout=GITHUB_PR_ALERTS_CACHE_TIMEOUT_SECONDS)
    return alerts


def _load_persistent_alert_rows(is_checked: bool):
    query_set = PersistentAlert.query.filter(
        PersistentAlert.is_checked.is_(bool(is_checked))
    )
    if is_checked:
        query_set = query_set.order_by(
            PersistentAlert.checked_at.desc(),
            PersistentAlert.last_detected_at.desc(),
            PersistentAlert.created_at.desc(),
        )
    else:
        query_set = query_set.order_by(
            PersistentAlert.last_detected_at.desc(),
            PersistentAlert.created_at.desc(),
        )
    return query_set.all()


def _count_alerts_by_source(rows, source_system: str):
    return sum(1 for row in rows if (row.source_system or '').strip() == source_system)


def get_open_alert_count() -> int:
    return (
        PersistentAlert.query
        .filter(PersistentAlert.is_checked.is_(False))
        .count()
    )


def get_alerts_payload(
    *,
    refresh_pipeline_snapshot=True,
    running_builds=None,
):
    if refresh_pipeline_snapshot:
        try:
            refresh_pipeline_storage_from_jenkins(
                include_quality_metrics=False,
                include_quality_backfill=False,
            )
        except Exception:
            current_app.logger.exception(
                'Failed to refresh pipeline snapshot for the alerts page.'
            )

    pipeline_context = _main_pipeline_context()
    branch_name = pipeline_context.get('branch_name') or 'main'
    builds = pipeline_context.get('builds') or []

    observed_alerts = []
    observed_alerts.extend(_build_failure_streak_alerts(branch_name, builds))
    observed_alerts.extend(
        _stage_duration_over_average_alerts(builds, running_builds=running_builds)
    )
    observed_alerts.extend(_stale_open_pull_request_alerts(branch_name))
    observed_alerts.extend(_prometheus_threshold_alerts())

    subscription_id = Config.AZURE_SUBSCRIPTION_ID
    if subscription_id:
        now = _utcnow()
        try:
            sync_finops_daily_cost_threshold_alerts(subscription_id, now.year, now.month)
        except Exception:
            current_app.logger.exception(
                'Failed to sync FinOps threshold alerts for the alerts page.'
            )

    if observed_alerts:
        _sync_persistent_alerts(observed_alerts)

    # Unchecked alerts intentionally stay open even after the live signal clears.
    open_rows = _load_persistent_alert_rows(is_checked=False)
    _enrich_persistent_alert_rows(open_rows)
    open_alerts = [_serialize_persistent_alert(row) for row in open_rows]
    generated_at = int(_utcnow().timestamp() * 1000)

    return {
        'connected': True,
        'pipeline': pipeline_context.get('pipeline') or {},
        'summary': {
            'alert_count': len(open_alerts),
            'finops_alert_count': _count_alerts_by_source(open_rows, 'finops'),
            'github_alert_count': _count_alerts_by_source(open_rows, 'github'),
            'prometheus_alert_count': _count_alerts_by_source(open_rows, 'prometheus'),
            'jenkins_alert_count': _count_alerts_by_source(open_rows, 'jenkins'),
            'build_alert_count': _count_alerts_by_source(open_rows, 'jenkins'),
            'pr_threshold_ms': PULL_REQUEST_STALE_THRESHOLD_MS,
            'failure_streak_threshold': BUILD_FAILURE_STREAK_THRESHOLD,
            'prometheus_threshold_pct': PROMETHEUS_THRESHOLD_PERCENT,
        },
        'alerts': open_alerts,
        'generated_at': generated_at,
    }
