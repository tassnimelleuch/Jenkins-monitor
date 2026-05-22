from datetime import datetime, timedelta, timezone
import re
from collections.abc import Mapping

from flask import current_app
from pipeline_identity import configured_branch_name, configured_pipeline_job_path, pipeline_name
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from extensions import cache, db
from pipeline_storage_models import (
    PipelineBranch,
    PipelineMainBuild,
    PipelineMainBuildStage,
)


PIPELINE_SNAPSHOT_CACHE_VERSION = 'v5'
PIPELINE_SNAPSHOT_CACHE_TIMEOUT_SECONDS = 86400
PIPELINE_COVERAGE_TREND_HISTORY_LIMIT = 120
DEPLOY_STAGE_NAME = 'Deploy to AKS'
ROLLOUT_STAGE_NAME = 'Wait for AKS Rollout'

_TESTS_DURATION_STAGE_NAME_NORMALIZER = re.compile(r'[^a-z0-9]+')
_NON_UNIT_TEST_STAGE_MARKERS = (
    'integration',
    'e2e',
    'smoke',
    'acceptance',
    'performance',
    'load',
)


def _utcnow():
    return datetime.now(timezone.utc)


def _millis_to_datetime(value):
    if value in (None, '', 0):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _datetime_to_millis(value):
    if not value:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _configured_selected_branch_name():
    return configured_branch_name(current_app.config, default='main')


def _configured_pipeline_job_path():
    return configured_pipeline_job_path(current_app.config, default_branch='main')


def _stored_pipeline_cache_key():
    return (
        f'pipeline_snapshot:{PIPELINE_SNAPSHOT_CACHE_VERSION}:'
        f'{_configured_pipeline_job_path()}:{_configured_selected_branch_name()}'
    )


def _stored_overview_cache_key():
    return (
        f'pipeline_overview:{PIPELINE_SNAPSHOT_CACHE_VERSION}:'
        f'{_configured_pipeline_job_path()}:{_configured_selected_branch_name()}'
    )


def _cache_snapshot_value(key, value):
    cache.set(key, value, timeout=PIPELINE_SNAPSHOT_CACHE_TIMEOUT_SECONDS)


def _clear_pipeline_snapshot_cache():
    cache.delete(_stored_pipeline_cache_key())
    cache.delete(_stored_overview_cache_key())


def _normalize_tests_duration_stage_name(stage_name):
    return _TESTS_DURATION_STAGE_NAME_NORMALIZER.sub(
        ' ',
        (stage_name or '').strip().lower(),
    ).strip()


def _classify_tests_duration_stage(stage_name):
    normalized = _normalize_tests_duration_stage_name(stage_name)
    if not normalized:
        return None

    if 'pylint' in normalized:
        return 'pylint'

    if 'sonar' in normalized:
        return 'sonarcloud'

    if 'pytest' in normalized:
        return 'unit_tests'

    if 'unit' in normalized and 'test' in normalized:
        return 'unit_tests'

    if 'test' in normalized and not any(marker in normalized for marker in _NON_UNIT_TEST_STAGE_MARKERS):
        return 'unit_tests'

    return None


def _extract_stage_name(stage):
    if isinstance(stage, Mapping):
        return (stage.get('stage_name') or stage.get('name') or '').strip()
    return (getattr(stage, 'stage_name', None) or getattr(stage, 'name', None) or '').strip()


def _extract_stage_duration_ms(stage):
    if isinstance(stage, Mapping):
        return int(stage.get('duration_ms') or 0)
    return int(getattr(stage, 'duration_ms', 0) or 0)


def _extract_stage_status(stage):
    if isinstance(stage, Mapping):
        return (stage.get('status') or '').strip().upper()
    return (getattr(stage, 'status', None) or '').strip().upper()


def _build_stages_from_payload(build):
    if isinstance(build, Mapping):
        return build.get('stages') or []
    return getattr(build, 'stages', []) or []


def _build_number_from_payload(build):
    if isinstance(build, Mapping):
        build_number = build.get('build_number')
        if build_number is None:
            build_number = build.get('number')
        return build_number

    build_number = getattr(build, 'build_number', None)
    if build_number is None:
        build_number = getattr(build, 'number', None)
    return build_number


def _build_status_from_payload(build):
    if isinstance(build, Mapping):
        return build.get('status')
    return getattr(build, 'status', None)


def _build_timestamp_from_payload(build):
    if isinstance(build, Mapping):
        timestamp = build.get('timestamp_ms')
        if timestamp is None:
            timestamp = build.get('timestamp', 0)
        return timestamp or 0

    timestamp = getattr(build, 'timestamp_ms', None)
    if timestamp is None:
        timestamp = getattr(build, 'timestamp', 0)
    return timestamp or 0


def _build_result_from_payload(build):
    if isinstance(build, Mapping):
        return build.get('result')
    return getattr(build, 'result', None)


def _build_runs_test_related_stages(build):
    return any(
        _classify_tests_duration_stage(_extract_stage_name(stage)) is not None
        for stage in _build_stages_from_payload(build)
    )


def build_tests_duration_points(builds, branch_name=None, finished_only=False, include_empty=False):
    points = []

    for build in builds or []:
        result = _build_result_from_payload(build)
        stages = _build_stages_from_payload(build)
        build_number = _build_number_from_payload(build)
        timestamp = _build_timestamp_from_payload(build)

        if finished_only and result is None:
            continue

        totals = {
            'unit_tests_ms': 0,
            'pylint_ms': 0,
            'sonarcloud_ms': 0,
        }
        matched_stage_count = 0
        for stage in stages:
            bucket = _classify_tests_duration_stage(_extract_stage_name(stage))
            if bucket is None:
                continue

            matched_stage_count += 1
            totals[f'{bucket}_ms'] += _extract_stage_duration_ms(stage)

        total_duration_ms = sum(totals.values()) if matched_stage_count else None
        if not include_empty and total_duration_ms is None:
            continue

        points.append({
            'branch': branch_name,
            'number': build_number,
            'timestamp': timestamp or 0,
            'result': result,
            'total_duration_ms': total_duration_ms,
            'matched_stage_count': matched_stage_count,
            **totals,
        })

    return points


def build_stage_success_frequency(builds, stage_name_contains):
    marker = (stage_name_contains or '').strip().lower()
    successful = 0
    total = 0

    for build in builds or []:
        if _build_result_from_payload(build) is None:
            continue

        total += 1
        stages = _build_stages_from_payload(build)

        if any(
            marker in _extract_stage_name(stage).lower()
            and _extract_stage_status(stage) == 'SUCCESS'
            for stage in stages
        ):
            successful += 1

    return {
        'successful': successful,
        'total': total,
        'rate': round((successful / total) * 100, 1) if total > 0 else 0,
    }


def _load_primary_branch_row():
    row = (
        PipelineBranch.query
        .order_by(PipelineBranch.is_primary.desc(), PipelineBranch.name.asc())
        .first()
    )
    if row is not None:
        return row
    return None


def _load_main_build_rows(branch_name):
    if not branch_name:
        return []

    return (
        PipelineMainBuild.query
        .options(selectinload(PipelineMainBuild.stages))
        .filter_by(branch_name=branch_name)
        .order_by(PipelineMainBuild.build_number.desc())
        .all()
    )


def _duration_ms_from_source(source):
    if not source:
        return 0

    duration_ms = source.get('duration_ms')
    if duration_ms is not None:
        return int(duration_ms or 0)

    duration = source.get('duration')
    if duration is not None:
        duration_seconds = source.get('duration_seconds')
        if duration_seconds is None:
            duration_seconds = duration
        return int(duration_seconds or 0) * 1000

    duration_seconds = source.get('duration_seconds')
    if duration_seconds is not None:
        return int(duration_seconds or 0) * 1000

    return 0


def _serialize_summary_build_from_row(row, branch_name):
    if row is None:
        return None

    return {
        'branch': branch_name,
        'number': row.build_number,
        'status': row.status,
        'result': row.result,
        'timestamp': row.timestamp_ms or 0,
        'duration_ms': row.duration_ms or 0,
        'duration_seconds': row.duration_seconds or 0,
    }


def _serialize_summary_build_from_branch_row(branch_row, completed=False):
    if branch_row is None:
        return None

    if completed:
        number = branch_row.last_completed_build_number
        result = branch_row.last_completed_build_result
        timestamp_ms = branch_row.last_completed_build_timestamp_ms
        duration_ms = branch_row.last_completed_build_duration_ms
    else:
        number = branch_row.last_build_number
        result = branch_row.last_build_result
        timestamp_ms = branch_row.last_build_timestamp_ms
        duration_ms = branch_row.last_build_duration_ms

    if number is None:
        return None

    duration_ms = int(duration_ms or 0)
    return {
        'branch': branch_row.name,
        'number': number,
        'status': None,
        'result': result,
        'timestamp': int(timestamp_ms or 0),
        'duration_ms': duration_ms,
        'duration_seconds': int(duration_ms / 1000) if duration_ms else 0,
    }


def _serialize_detailed_build_from_row(row, branch_name):
    payload = _serialize_summary_build_from_row(row, branch_name)
    if payload is None:
        return None

    payload.update({
        'coverage_percent': row.coverage_percent,
        'junit_total': row.junit_total,
        'junit_passed': row.junit_passed,
        'junit_failed': row.junit_failed,
        'junit_skipped': row.junit_skipped,
        'stages': [
            {
                'name': stage.stage_name,
                'status': stage.status,
                'duration_ms': stage.duration_ms or 0,
                'start_time': _datetime_to_millis(stage.started_at),
            }
            for stage in sorted(
                row.stages,
                key=lambda item: (
                    item.started_at or datetime.min.replace(tzinfo=timezone.utc),
                    item.id or 0,
                ),
            )
        ],
    })
    return payload


def _build_stage_failure_rates(build_rows):
    finished_rows = [row for row in build_rows if row.result is not None]
    stage_totals = {}
    stage_failures = {}

    for row in finished_rows:
        for stage in row.stages or []:
            stage_name = (stage.stage_name or '').strip()
            if not stage_name:
                continue
            stage_totals[stage_name] = stage_totals.get(stage_name, 0) + 1
            if (stage.status or '').strip().upper() == 'FAILED':
                stage_failures[stage_name] = stage_failures.get(stage_name, 0) + 1

    return {
        stage_name: round((stage_failures.get(stage_name, 0) / total) * 100, 1)
        for stage_name, total in sorted(stage_totals.items())
        if total > 0
    }


def _build_deployment_frequency(build_rows):
    successful = 0
    total = 0

    for row in build_rows:
        if row.result is None:
            continue

        total += 1
        stage_map = {
            (stage.stage_name or '').strip(): (stage.status or '').strip().upper()
            for stage in row.stages or []
        }
        deploy_ok = stage_map.get(DEPLOY_STAGE_NAME) == 'SUCCESS'
        rollout_ok = stage_map.get(ROLLOUT_STAGE_NAME) == 'SUCCESS'
        if deploy_ok and rollout_ok:
            successful += 1

    return {
        'successful': successful,
        'total': total,
        'rate': round((successful / total) * 100, 1) if total > 0 else 0,
    }


def _build_branch_summary(branch_row, build_rows):
    finished_rows = [row for row in build_rows if row.result is not None]
    successful = sum(1 for row in finished_rows if row.result == 'SUCCESS')
    failed = sum(1 for row in finished_rows if row.result == 'FAILURE')
    aborted = sum(1 for row in finished_rows if row.result == 'ABORTED')
    running = sum(1 for row in build_rows if row.result is None)
    finished_count = successful + failed + aborted

    durations = [row.duration_ms for row in finished_rows if (row.duration_ms or 0) > 0]
    avg_duration_ms = int(sum(durations) / len(durations)) if durations else 0
    avg_duration_seconds = int(avg_duration_ms / 1000) if avg_duration_ms else 0
    success_rate = round((successful / finished_count) * 100, 1) if finished_count > 0 else 0

    last_build_row = build_rows[0] if build_rows else None
    last_completed_row = next((row for row in build_rows if row.result is not None), None)

    return {
        'last_build_number': (
            branch_row.last_build_number
            or (last_build_row.build_number if last_build_row is not None else None)
        ),
        'last_completed_build_number': (
            branch_row.last_completed_build_number
            or (last_completed_row.build_number if last_completed_row is not None else None)
        ),
        'total_builds': len(finished_rows),
        'successful': successful,
        'failed': failed,
        'aborted': aborted,
        'running': running,
        'success_rate': success_rate,
        'health_score': branch_row.health_score if branch_row.health_score is not None else 0,
        'avg_duration_ms': avg_duration_ms,
        'avg_duration_seconds': avg_duration_seconds,
    }


def _avg_test_coverage(build_rows):
    values = [row.coverage_percent for row in build_rows if row.coverage_percent is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _branch_overview_payload(branch_row, selected_branch):
    return {
        'name': branch_row.name,
        'selected': branch_row.name == selected_branch,
        'summary': {
            'health_score': branch_row.health_score if branch_row.health_score is not None else 0,
            'last_build_number': branch_row.last_build_number,
            'last_completed_build_number': branch_row.last_completed_build_number,
        },
        'status': {
            'color': branch_row.status_color,
            'building': bool(branch_row.is_building),
        },
        'last_build': _serialize_summary_build_from_branch_row(branch_row, completed=False),
        'last_completed_build': _serialize_summary_build_from_branch_row(branch_row, completed=True),
        'links': {
            'job_url': branch_row.job_url,
        },
    }


def _selected_branch_payload(branch_row, selected_branch, build_rows):
    summary = _build_branch_summary(branch_row, build_rows)
    last_build = (
        _serialize_summary_build_from_row(build_rows[0], branch_row.name)
        if build_rows
        else _serialize_summary_build_from_branch_row(branch_row, completed=False)
    )
    last_completed = next((row for row in build_rows if row.result is not None), None)

    finished_rows = [row for row in build_rows if row.result is not None]
    coverage_rows = list(reversed(finished_rows[:PIPELINE_COVERAGE_TREND_HISTORY_LIMIT]))
    junit_rows = list(reversed(finished_rows))

    return {
        'name': branch_row.name,
        'selected': branch_row.name == selected_branch,
        'summary': summary,
        'status': {
            'color': branch_row.status_color,
            'building': bool(branch_row.is_building),
        },
        'last_build': last_build,
        'last_completed_build': (
            _serialize_summary_build_from_row(last_completed, branch_row.name)
            if last_completed is not None
            else _serialize_summary_build_from_branch_row(branch_row, completed=True)
        ),
        'links': {
            'job_url': branch_row.job_url,
        },
        'builds': [
            _serialize_detailed_build_from_row(row, branch_row.name)
            for row in build_rows
        ],
        'trends': {
            'builds': [
                _serialize_summary_build_from_row(row, branch_row.name)
                for row in build_rows
            ],
            'durations': [
                {
                    'branch': branch_row.name,
                    'number': row.build_number,
                    'duration_seconds': row.duration_seconds or 0,
                    'duration_ms': row.duration_ms or 0,
                }
                for row in junit_rows
            ],
            'coverage': [
                {
                    'branch': branch_row.name,
                    'number': row.build_number,
                    'coverage': row.coverage_percent,
                    'timestamp': row.timestamp_ms or 0,
                }
                for row in coverage_rows
            ],
            'junit': [
                {
                    'branch': branch_row.name,
                    'number': row.build_number,
                    'timestamp': row.timestamp_ms or 0,
                    'total': row.junit_total,
                    'passed': row.junit_passed,
                    'failed': row.junit_failed,
                    'skipped': row.junit_skipped,
                }
                for row in junit_rows
            ],
            'tests_duration': build_tests_duration_points(
                build_rows,
                branch_name=branch_row.name,
                finished_only=True,
            ),
        },
        'stages': {
            'failure_rate': _build_stage_failure_rates(build_rows),
        },
        'quality': {
            'avg_test_coverage': _avg_test_coverage(finished_rows),
        },
        'deployment': {
            'frequency': _build_deployment_frequency(build_rows),
        },
    }


def _load_stored_pipeline_kpis():
    branch_rows = (
        PipelineBranch.query
        .order_by(PipelineBranch.is_primary.desc(), PipelineBranch.name.asc())
        .all()
    )
    if not branch_rows:
        return None

    primary_row = next((row for row in branch_rows if row.is_primary), None) or branch_rows[0]
    selected_branch = primary_row.name
    main_build_rows = _load_main_build_rows(selected_branch)

    branches = {}
    for row in branch_rows:
        if row.name == selected_branch:
            branches[row.name] = _selected_branch_payload(row, selected_branch, main_build_rows)
        else:
            branches[row.name] = _branch_overview_payload(row, selected_branch)

    return {
        'connected': True,
        'pipeline': {
            'name': pipeline_name(
                current_app.config.get('JENKINS_JOB'),
                branch_name=selected_branch,
            ),
            'type': 'multibranch' if len(branch_rows) > 1 else 'single-branch',
            'selected_branch': selected_branch,
        },
        'branches': branches,
    }


def get_stored_pipeline_kpis():
    cached = cache.get(_stored_pipeline_cache_key())
    if cached is not None:
        return cached

    stored = _load_stored_pipeline_kpis()
    if stored is not None:
        _cache_snapshot_value(_stored_pipeline_cache_key(), stored)
    return stored


def get_overview_kpis_from_stored_pipeline(stored):
    if not stored:
        return None

    pipeline = stored.get('pipeline') or {}
    branches = stored.get('branches') or {}
    selected_branch = pipeline.get('selected_branch')
    branch_data = branches.get(selected_branch) if selected_branch else None
    if not branch_data:
        return None

    summary = branch_data.get('summary') or {}
    detailed_builds = branch_data.get('builds') or []
    trend_builds = (branch_data.get('trends') or {}).get('builds') or []
    cutoff_ms = int((_utcnow() - timedelta(hours=24)).timestamp() * 1000)
    source_builds = detailed_builds or trend_builds
    build_trend = [
        {
            'branch': item.get('branch'),
            'number': item.get('number'),
            'status': item.get('status'),
            'result': item.get('result'),
            'timestamp': item.get('timestamp', 0),
            'duration': item.get('duration_ms', item.get('duration', 0)),
            'duration_ms': item.get('duration_ms', item.get('duration', 0)),
            'duration_seconds': item.get('duration_seconds', 0),
            'stages': item.get('stages') or [],
        }
        for item in source_builds
        if item.get('result') is None or (item.get('timestamp', 0) or 0) >= cutoff_ms
    ]

    return {
        'connected': True,
        'last_build_number': summary.get('last_build_number'),
        'total_builds': summary.get('total_builds'),
        'successful': summary.get('successful'),
        'failed': summary.get('failed'),
        'aborted': summary.get('aborted'),
        'running': summary.get('running'),
        'success_rate': summary.get('success_rate'),
        'health_score': summary.get('health_score'),
        'build_trend': build_trend,
        'avg_duration_ms': summary.get('avg_duration_ms'),
        'tests_duration': (branch_data.get('trends') or {}).get('tests_duration') or [],
    }


def get_stored_overview_kpis():
    cached = cache.get(_stored_overview_cache_key())
    if cached is not None:
        return cached

    stored = get_stored_pipeline_kpis()
    overview = get_overview_kpis_from_stored_pipeline(stored)
    if overview is not None:
        _cache_snapshot_value(_stored_overview_cache_key(), overview)
    return overview


def warm_pipeline_snapshot_cache():
    stored = _load_stored_pipeline_kpis()
    if stored is None:
        _clear_pipeline_snapshot_cache()
        return None

    _cache_snapshot_value(_stored_pipeline_cache_key(), stored)

    overview = get_overview_kpis_from_stored_pipeline(stored)
    if overview is not None:
        _cache_snapshot_value(_stored_overview_cache_key(), overview)
    else:
        cache.delete(_stored_overview_cache_key())

    return stored


def get_stored_branch_stage_success_frequency(branch_name=None, stage_name_contains='deploy to aks'):
    primary_row = _load_primary_branch_row()
    if primary_row is None:
        return {'successful': 0, 'total': 0, 'rate': 0}

    clean_branch_name = (branch_name or '').strip() or primary_row.name
    if clean_branch_name != primary_row.name:
        return {'successful': 0, 'total': 0, 'rate': 0}

    build_rows = _load_main_build_rows(primary_row.name)
    return build_stage_success_frequency(build_rows, stage_name_contains)


def _initial_prepared_build_payload(build_number):
    return {
        'number': build_number,
        'status': None,
        'result': None,
        'timestamp_ms': 0,
        'duration_seconds': 0,
        'duration_ms': 0,
        'is_last_build': False,
        'is_last_completed_build': False,
        'stages': None,
        'coverage_percent': None,
        'has_coverage_percent': False,
        'junit_total': None,
        'junit_passed': None,
        'junit_failed': None,
        'junit_skipped': None,
        'has_junit_report': False,
    }


def _ensure_prepared_build_payload(prepared, build_number):
    if build_number is None:
        return None
    payload = prepared.get(build_number)
    if payload is None:
        payload = _initial_prepared_build_payload(build_number)
        prepared[build_number] = payload
    return payload


def _apply_build_core_fields(payload, source):
    duration_ms = _duration_ms_from_source(source)
    duration_seconds = source.get('duration_seconds')
    if duration_seconds is None:
        duration_seconds = int(duration_ms / 1000) if duration_ms else int(source.get('duration') or 0)

    payload.update({
        'status': source.get('status'),
        'result': source.get('result'),
        'timestamp_ms': source.get('timestamp', 0) or source.get('timestamp_ms', 0) or 0,
        'duration_seconds': int(duration_seconds or 0),
        'duration_ms': int(duration_ms or 0),
    })


def _prepare_branch_build_payloads(branch_payload):
    prepared = {}
    trends = branch_payload.get('trends') or {}
    coverage_map = {
        item.get('number'): item
        for item in (trends.get('coverage') or [])
        if item.get('number') is not None
    }
    junit_map = {
        item.get('number'): item
        for item in (trends.get('junit') or [])
        if item.get('number') is not None
    }

    for build in (branch_payload.get('builds') or []):
        number = build.get('number')
        payload = _ensure_prepared_build_payload(prepared, number)
        if payload is None:
            continue
        _apply_build_core_fields(payload, build)
        # Preserve omitted stage payloads during fast running-build refreshes so
        # we do not wipe stored historical stages with an artificial empty list.
        payload['stages'] = build.get('stages')
        if build.get('coverage_percent') is not None:
            payload['coverage_percent'] = build.get('coverage_percent')
            payload['has_coverage_percent'] = True
        if any(
            build.get(key) is not None
            for key in ('junit_total', 'junit_passed', 'junit_failed', 'junit_skipped')
        ):
            payload['junit_total'] = build.get('junit_total')
            payload['junit_passed'] = build.get('junit_passed')
            payload['junit_failed'] = build.get('junit_failed')
            payload['junit_skipped'] = build.get('junit_skipped')
            payload['has_junit_report'] = True

    for summary_key, flag_key in (
        ('last_build', 'is_last_build'),
        ('last_completed_build', 'is_last_completed_build'),
    ):
        summary_build = branch_payload.get(summary_key) or {}
        number = summary_build.get('number')
        payload = _ensure_prepared_build_payload(prepared, number)
        if payload is None:
            continue
        _apply_build_core_fields(payload, summary_build)
        payload[flag_key] = True

    for number, item in coverage_map.items():
        payload = _ensure_prepared_build_payload(prepared, number)
        if payload is None:
            continue
        payload['coverage_percent'] = item.get('coverage')
        payload['has_coverage_percent'] = item.get('coverage') is not None

    for number, item in junit_map.items():
        payload = _ensure_prepared_build_payload(prepared, number)
        if payload is None:
            continue
        payload['junit_total'] = item.get('total')
        payload['junit_passed'] = item.get('passed')
        payload['junit_failed'] = item.get('failed')
        payload['junit_skipped'] = item.get('skipped')
        payload['has_junit_report'] = any(
            item.get(key) is not None
            for key in ('total', 'passed', 'failed', 'skipped')
        )

    return prepared


def _apply_optional_build_quality_fields(row, payload):
    if payload.get('has_coverage_percent'):
        row.coverage_percent = payload.get('coverage_percent')

    if payload.get('has_junit_report'):
        row.junit_total = payload.get('junit_total')
        row.junit_passed = payload.get('junit_passed')
        row.junit_failed = payload.get('junit_failed')
        row.junit_skipped = payload.get('junit_skipped')


def _build_end_datetime(payload):
    timestamp_ms = int(payload.get('timestamp_ms') or 0)
    duration_ms = int(payload.get('duration_ms') or 0)
    if payload.get('result') is None or timestamp_ms <= 0:
        return None
    return _millis_to_datetime(timestamp_ms + max(duration_ms, 0))


def _sync_build_stages(build_row, stages):
    existing = {
        row.stage_name: row
        for row in PipelineMainBuildStage.query.filter_by(build_number=build_row.build_number).all()
    }
    incoming_names = set()

    for stage in stages or []:
        stage_name = (stage.get('name') or '').strip()
        if not stage_name:
            continue

        incoming_names.add(stage_name)
        row = existing.get(stage_name)
        if row is None:
            row = PipelineMainBuildStage(
                build_number=build_row.build_number,
                stage_name=stage_name,
            )
            db.session.add(row)

        row.status = stage.get('status')
        row.started_at = _millis_to_datetime(stage.get('start_time'))
        row.duration_ms = int(stage.get('duration_ms') or 0)

    for stage_name, row in existing.items():
        if stage_name not in incoming_names:
            db.session.delete(row)


def _resolve_primary_branch_name(branches_payload, selected_branch):
    clean_selected_branch = (selected_branch or '').strip()
    if 'main' in branches_payload:
        return 'main'
    if clean_selected_branch and clean_selected_branch in branches_payload:
        return clean_selected_branch
    return next(iter(branches_payload), clean_selected_branch or 'main')


def _sync_main_branch_builds(primary_branch_name, branch_payload, now):
    prepared = _prepare_branch_build_payloads(branch_payload)
    existing = {
        row.build_number: row
        for row in PipelineMainBuild.query.all()
    }

    for build_number, row in list(existing.items()):
        if build_number in prepared and row.branch_name == primary_branch_name:
            continue
        db.session.delete(row)
        existing.pop(build_number, None)

    for build_number, payload in prepared.items():
        row = existing.get(build_number)
        if row is None:
            row = PipelineMainBuild(
                build_number=build_number,
                branch_name=primary_branch_name,
            )
            db.session.add(row)

        row.branch_name = primary_branch_name
        row.status = payload.get('status')
        row.result = payload.get('result')
        row.is_running = payload.get('result') is None
        row.is_last_build = bool(payload.get('is_last_build'))
        row.is_last_completed_build = bool(payload.get('is_last_completed_build'))
        row.timestamp_ms = int(payload.get('timestamp_ms') or 0)
        row.started_at = _millis_to_datetime(payload.get('timestamp_ms'))
        row.ended_at = _build_end_datetime(payload)
        row.duration_seconds = int(payload.get('duration_seconds') or 0)
        row.duration_ms = int(payload.get('duration_ms') or (row.duration_seconds * 1000))
        row.last_synced_at = now
        _apply_optional_build_quality_fields(row, payload)

        db.session.flush()
        if payload.get('stages') is not None:
            _sync_build_stages(row, payload.get('stages') or [])


def sync_pipeline_snapshot(payload):
    if not payload or not payload.get('connected'):
        return False

    pipeline_payload = payload.get('pipeline') or {}
    branches_payload = dict(payload.get('branches') or {})
    if not branches_payload:
        return False

    now = _utcnow()
    primary_branch_name = _resolve_primary_branch_name(
        branches_payload,
        pipeline_payload.get('selected_branch') or _configured_selected_branch_name(),
    )

    try:
        existing_branches = {
            row.name: row
            for row in PipelineBranch.query.all()
        }
        incoming_branch_names = set(branches_payload)

        for branch_name, row in list(existing_branches.items()):
            if branch_name in incoming_branch_names:
                continue
            db.session.delete(row)
            existing_branches.pop(branch_name, None)

        for branch_name, branch_payload in branches_payload.items():
            row = existing_branches.get(branch_name)
            if row is None:
                row = PipelineBranch(name=branch_name)
                db.session.add(row)

            summary = branch_payload.get('summary') or {}
            status = branch_payload.get('status') or {}
            links = branch_payload.get('links') or {}
            last_build = branch_payload.get('last_build') or {}
            last_completed = branch_payload.get('last_completed_build') or {}

            row.job_name = branch_payload.get('job_name') or branch_name
            row.job_url = links.get('job_url')
            row.is_primary = branch_name == primary_branch_name
            row.status_color = status.get('color')
            row.is_building = bool(status.get('building'))
            row.health_score = summary.get('health_score')
            row.last_build_number = summary.get('last_build_number') or last_build.get('number')
            row.last_build_result = last_build.get('result')
            row.last_build_timestamp_ms = (
                int(last_build.get('timestamp') or 0)
                if (last_build.get('timestamp') is not None)
                else None
            )
            row.last_build_duration_ms = _duration_ms_from_source(last_build) if last_build else None
            row.last_completed_build_number = (
                summary.get('last_completed_build_number')
                or last_completed.get('number')
            )
            row.last_completed_build_result = last_completed.get('result')
            row.last_completed_build_timestamp_ms = (
                int(last_completed.get('timestamp') or 0)
                if (last_completed.get('timestamp') is not None)
                else None
            )
            row.last_completed_build_duration_ms = (
                _duration_ms_from_source(last_completed) if last_completed else None
            )
            row.last_synced_at = now

        primary_branch_payload = branches_payload.get(primary_branch_name) or {}
        _sync_main_branch_builds(primary_branch_name, primary_branch_payload, now)

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to sync pipeline branches/main builds into the database.'
        )
        return False

    warm_pipeline_snapshot_cache()
    return True


def _empty_test_backfill_result():
    return {
        'candidate_builds': 0,
        'coverage_updates': 0,
        'junit_updates': 0,
    }


def _load_main_build_rows_by_number(build_numbers):
    if not build_numbers:
        return {}

    rows = (
        PipelineMainBuild.query
        .options(selectinload(PipelineMainBuild.stages))
        .filter(PipelineMainBuild.build_number.in_(build_numbers))
        .all()
    )
    return {row.build_number: row for row in rows}


def _row_needs_junit_backfill(row):
    return any(
        value is None
        for value in (
            row.junit_total,
            row.junit_passed,
            row.junit_failed,
            row.junit_skipped,
        )
    )


def _build_needs_quality_backfill(build, row):
    if row is None:
        return False

    if _build_result_from_payload(build) is None:
        return False

    if not _build_runs_test_related_stages(build):
        return False

    return row.coverage_percent is None or _row_needs_junit_backfill(row)


def backfill_branch_test_results(
    branch_name,
    builds,
    coverage_fetcher,
    test_report_fetcher,
):
    if not builds:
        return _empty_test_backfill_result()

    primary_row = _load_primary_branch_row()
    if primary_row is None:
        return _empty_test_backfill_result()

    clean_branch_name = (branch_name or '').strip() or primary_row.name
    if clean_branch_name != primary_row.name:
        return _empty_test_backfill_result()

    build_numbers = [
        _build_number_from_payload(build)
        for build in builds or []
        if _build_number_from_payload(build) is not None
    ]
    row_map = _load_main_build_rows_by_number(build_numbers)
    coverage_updates = 0
    junit_updates = 0
    candidate_builds = 0

    for build in builds or []:
        build_number = _build_number_from_payload(build)
        row = row_map.get(build_number)
        if not _build_needs_quality_backfill(build, row):
            continue

        candidate_builds += 1

        if row.coverage_percent is None:
            try:
                coverage = coverage_fetcher(build_number)
            except Exception:
                current_app.logger.exception(
                    'Coverage backfill failed for build #%s.',
                    build_number,
                )
                coverage = None
            if coverage is not None:
                row.coverage_percent = coverage
                coverage_updates += 1

        if _row_needs_junit_backfill(row):
            try:
                report = test_report_fetcher(build_number)
            except Exception:
                current_app.logger.exception(
                    'JUnit backfill failed for build #%s.',
                    build_number,
                )
                report = None
            if report is not None:
                row.junit_total = report.get('total')
                row.junit_passed = report.get('passed')
                row.junit_failed = report.get('failed')
                row.junit_skipped = report.get('skipped')
                junit_updates += 1

    if coverage_updates == 0 and junit_updates == 0:
        return {
            'candidate_builds': candidate_builds,
            'coverage_updates': 0,
            'junit_updates': 0,
        }

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to commit main-branch quality backfill updates.'
        )
        return _empty_test_backfill_result()

    warm_pipeline_snapshot_cache()
    return {
        'candidate_builds': candidate_builds,
        'coverage_updates': coverage_updates,
        'junit_updates': junit_updates,
    }
