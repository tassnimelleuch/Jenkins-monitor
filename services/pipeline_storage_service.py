from datetime import datetime, timedelta, timezone
import re
from collections.abc import Mapping

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from extensions import cache, db
from pipeline_storage_models import (
    PipelineBranch,
    PipelineBranchBuild,
    PipelineBranchBuildStage,
    PipelineBranchStageKpi,
    PipelineBuildDuration,
    PipelineDefinition,
    PipelineStageDuration,
)
from services.parallel_executor import parallel_execute

PIPELINE_SNAPSHOT_CACHE_VERSION = 'v3'
PIPELINE_SNAPSHOT_CACHE_TIMEOUT_SECONDS = 86400
PIPELINE_COVERAGE_TREND_HISTORY_LIMIT = 120
PIPELINE_JUNIT_TREND_HISTORY_LIMIT = 20
PIPELINE_TEST_HISTORY_BACKFILL_BATCH_SIZE = 40
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
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _datetime_to_millis(value):
    if not value:
        return 0
    return int(value.timestamp() * 1000)


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


def build_tests_duration_points(builds, branch_name=None, finished_only=False, include_empty=False):
    points = []

    for build in builds or []:
        if isinstance(build, Mapping):
            result = build.get('result')
            stages = build.get('stages') or []
            build_number = build.get('build_number')
            if build_number is None:
                build_number = build.get('number')
            timestamp = build.get('timestamp_ms')
            if timestamp is None:
                timestamp = build.get('timestamp', 0)
        else:
            result = getattr(build, 'result', None)
            stages = getattr(build, 'stages', []) or []
            build_number = getattr(build, 'build_number', None)
            if build_number is None:
                build_number = getattr(build, 'number', None)
            timestamp = getattr(build, 'timestamp_ms', None)
            if timestamp is None:
                timestamp = getattr(build, 'timestamp', 0)

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
        if isinstance(build, Mapping):
            stages = build.get('stages') or []
        else:
            stages = getattr(build, 'stages', []) or []

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


def get_stored_branch_stage_success_frequency(branch_name=None, stage_name_contains='deploy to aks'):
    pipeline = _load_pipeline_definition()
    if pipeline is None:
        return {'successful': 0, 'total': 0, 'rate': 0}

    clean_branch_name = (branch_name or '').strip() or _selected_branch_from_config()
    branch_row = PipelineBranch.query.filter_by(
        pipeline_id=pipeline.id,
        name=clean_branch_name,
    ).one_or_none()
    if branch_row is None:
        return {'successful': 0, 'total': 0, 'rate': 0}

    build_rows = (
        PipelineBranchBuild.query
        .options(selectinload(PipelineBranchBuild.stages))
        .filter_by(branch_id=branch_row.id)
        .order_by(PipelineBranchBuild.build_number.desc())
        .all()
    )
    return build_stage_success_frequency(build_rows, stage_name_contains)


def _chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


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


def _build_result_from_payload(build):
    if isinstance(build, Mapping):
        return build.get('result')
    return getattr(build, 'result', None)


def _build_runs_test_related_stages(build):
    if isinstance(build, Mapping):
        stages = build.get('stages') or []
    else:
        stages = getattr(build, 'stages', []) or []

    return any(
        _classify_tests_duration_stage(_extract_stage_name(stage)) is not None
        for stage in stages
    )


def _normalize_job_path(job_path):
    raw_job = (job_path or '').strip().strip('/')
    if not raw_job:
        return ''

    normalized = raw_job.replace('/job/', '/')
    if normalized.startswith('job/'):
        normalized = normalized[4:]

    return '/'.join(part for part in normalized.split('/') if part)


def _selected_branch_from_config():
    branch = (current_app.config.get('JENKINS_BRANCH') or 'main').strip().strip('/')
    return branch or 'main'


def _store_selected_branch_only():
    return bool(current_app.config.get('PIPELINE_STORE_SELECTED_BRANCH_ONLY', True))


def _pipeline_job_path_from_config():
    normalized = _normalize_job_path(current_app.config.get('JENKINS_JOB'))
    if not normalized:
        return ''

    parts = [part for part in normalized.split('/') if part]
    branch = _selected_branch_from_config()
    if branch and len(parts) > 1 and parts[-1] == branch:
        parts = parts[:-1]
    return '/'.join(parts)


def _pipeline_name_from_job_path(job_path):
    if not job_path:
        return 'Jenkins Pipeline'
    return job_path.split('/')[-1]


def _load_pipeline_definition():
    job_path = _pipeline_job_path_from_config()
    query = PipelineDefinition.query.filter_by(source_system='jenkins')
    if job_path:
        pipeline = query.filter_by(job_path=job_path).one_or_none()
        if pipeline is not None:
            return pipeline
    return query.order_by(PipelineDefinition.last_synced_at.desc()).first()


def _stored_pipeline_cache_key():
    job_path = _pipeline_job_path_from_config() or 'default'
    return f'pipeline_snapshot:{PIPELINE_SNAPSHOT_CACHE_VERSION}:{job_path}'


def _stored_overview_cache_key():
    job_path = _pipeline_job_path_from_config() or 'default'
    return f'overview_snapshot:{PIPELINE_SNAPSHOT_CACHE_VERSION}:{job_path}'


def _serialize_branch_build(build, branch_name):
    if build is None:
        return None

    return {
        'branch': branch_name,
        'number': build.build_number,
        'result': build.result,
        'timestamp': build.timestamp_ms or 0,
        'duration_ms': build.duration_ms or 0,
        'duration_seconds': build.duration_seconds or 0,
    }


def _serialize_detailed_build(build, branch_name):
    payload = _serialize_branch_build(build, branch_name)
    if payload is None:
        return None

    payload.update({
        'coverage_percent': build.coverage_percent,
        'junit_total': build.junit_total,
        'junit_passed': build.junit_passed,
        'junit_failed': build.junit_failed,
        'junit_skipped': build.junit_skipped,
    })
    payload['stages'] = [
        {
            'name': stage.stage_name,
            'status': stage.status,
            'duration_ms': stage.duration_ms or 0,
            'start_time': _datetime_to_millis(stage.started_at),
        }
        for stage in sorted(
            build.stages,
            key=lambda item: (item.started_at or datetime.min.replace(tzinfo=timezone.utc), item.stage_name),
        )
    ]
    return payload


def _build_branch_summary(branch_row, build_rows):
    finished_rows = [row for row in build_rows if row.result is not None]
    successful = sum(1 for row in finished_rows if row.result == 'SUCCESS')
    failed = sum(1 for row in finished_rows if row.result == 'FAILURE')
    aborted = sum(1 for row in finished_rows if row.result == 'ABORTED')
    running = sum(1 for row in build_rows if row.result is None)
    finished_count = successful + failed + aborted

    avg_duration_ms = branch_row.avg_duration_ms
    if avg_duration_ms is None:
        durations = [row.duration_ms for row in finished_rows if row.duration_ms and row.duration_ms > 0]
        avg_duration_ms = int(sum(durations) / len(durations)) if durations else 60000

    avg_duration_seconds = branch_row.avg_duration_seconds
    if avg_duration_seconds is None:
        avg_duration_seconds = int(avg_duration_ms / 1000) if avg_duration_ms else 0

    success_rate = round((successful / finished_count) * 100, 1) if finished_count > 0 else 0

    last_build = build_rows[0] if build_rows else None
    last_completed = next((row for row in build_rows if row.result is not None), None)

    return {
        'last_build_number': branch_row.last_build_number or (last_build.build_number if last_build else None),
        'last_completed_build_number': (
            branch_row.last_completed_build_number
            or (last_completed.build_number if last_completed else None)
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


def _branch_payload_from_row(branch_row, selected_branch):
    build_rows = (
        PipelineBranchBuild.query
        .filter_by(branch_id=branch_row.id)
        .order_by(PipelineBranchBuild.build_number.desc())
        .all()
    )
    summary = _build_branch_summary(branch_row, build_rows)

    last_build_row = next(
        (row for row in build_rows if row.build_number == summary['last_build_number']),
        build_rows[0] if build_rows else None,
    )
    last_completed_row = next(
        (row for row in build_rows if row.build_number == summary['last_completed_build_number']),
        next((row for row in build_rows if row.result is not None), None),
    )

    finished_rows = [row for row in build_rows if row.result is not None]
    coverage_rows = list(reversed(finished_rows[:PIPELINE_COVERAGE_TREND_HISTORY_LIMIT]))
    junit_rows = list(reversed(finished_rows[:PIPELINE_JUNIT_TREND_HISTORY_LIMIT]))

    return {
        'name': branch_row.name,
        'selected': branch_row.name == selected_branch,
        'summary': summary,
        'status': {
            'color': branch_row.status_color,
            'building': bool(branch_row.is_building),
        },
        'last_build': _serialize_branch_build(last_build_row, branch_row.name),
        'last_completed_build': _serialize_branch_build(last_completed_row, branch_row.name),
        'links': {
            'job_url': branch_row.job_url,
        },
        'builds': [
            _serialize_detailed_build(row, branch_row.name)
            for row in build_rows
        ],
        'trends': {
            'builds': [
                _serialize_branch_build(row, branch_row.name)
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
                finished_only=False,
            ),
        },
        'stages': {
            'failure_rate': {
                row.stage_name: row.failure_rate
                for row in sorted(branch_row.stage_kpis, key=lambda item: item.stage_name)
            },
        },
        'quality': {
            'avg_test_coverage': branch_row.avg_test_coverage,
        },
        'deployment': {
            'frequency': {
                'successful': branch_row.deployment_successful or 0,
                'total': branch_row.deployment_total or 0,
                'rate': branch_row.deployment_rate or 0,
            },
        },
    }


def _load_stored_pipeline_kpis():
    pipeline = _load_pipeline_definition()
    if pipeline is None:
        return None

    branch_rows = (
        PipelineBranch.query
        .filter_by(pipeline_id=pipeline.id)
        .order_by(PipelineBranch.is_selected.desc(), PipelineBranch.name.asc())
        .all()
    )
    if not branch_rows:
        return None

    selected_branch = (
        pipeline.selected_branch
        or next((row.name for row in branch_rows if row.is_selected), None)
        or branch_rows[0].name
    )
    branches = {
        row.name: _branch_payload_from_row(row, selected_branch)
        for row in branch_rows
    }

    return {
        'connected': True,
        'pipeline': {
            'name': pipeline.name,
            'type': pipeline.pipeline_type or ('multibranch' if len(branch_rows) > 1 else 'single-branch'),
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
        cache.set(
            _stored_pipeline_cache_key(),
            stored,
            timeout=PIPELINE_SNAPSHOT_CACHE_TIMEOUT_SECONDS,
        )
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
        cache.set(
            _stored_overview_cache_key(),
            overview,
            timeout=PIPELINE_SNAPSHOT_CACHE_TIMEOUT_SECONDS,
        )
    return overview


def warm_pipeline_snapshot_cache():
    stored = _load_stored_pipeline_kpis()
    if stored is None:
        cache.delete(_stored_pipeline_cache_key())
        cache.delete(_stored_overview_cache_key())
        return None

    cache.set(
        _stored_pipeline_cache_key(),
        stored,
        timeout=PIPELINE_SNAPSHOT_CACHE_TIMEOUT_SECONDS,
    )

    overview = get_overview_kpis_from_stored_pipeline(stored)
    if overview is not None:
        cache.set(
            _stored_overview_cache_key(),
            overview,
            timeout=PIPELINE_SNAPSHOT_CACHE_TIMEOUT_SECONDS,
        )
    else:
        cache.delete(_stored_overview_cache_key())

    return stored


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

    def ensure_payload(number):
        if number is None:
            return None
        payload = prepared.get(number)
        if payload is None:
            payload = {
                'number': number,
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
            prepared[number] = payload
        return payload

    for build in (branch_payload.get('builds') or []):
        number = build.get('number')
        payload = ensure_payload(number)
        if payload is None:
            continue
        payload.update({
            'result': build.get('result'),
            'timestamp_ms': build.get('timestamp', 0) or 0,
            'duration_seconds': build.get('duration_seconds', 0) or 0,
            'duration_ms': build.get('duration_ms', 0) or 0,
            'stages': build.get('stages') or [],
        })
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
        payload = ensure_payload(number)
        if payload is None:
            continue
        payload.update({
            'result': summary_build.get('result'),
            'timestamp_ms': summary_build.get('timestamp', 0) or 0,
            'duration_seconds': summary_build.get('duration_seconds', 0) or 0,
            'duration_ms': summary_build.get('duration_ms', 0) or 0,
        })
        payload[flag_key] = True

    for number, item in coverage_map.items():
        payload = ensure_payload(number)
        if payload is None:
            continue
        payload['coverage_percent'] = item.get('coverage')
        payload['has_coverage_percent'] = item.get('coverage') is not None

    for number, item in junit_map.items():
        payload = ensure_payload(number)
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


def _sync_branch_stage_kpis(branch_row, branch_payload):
    if 'stages' not in branch_payload:
        return

    stage_failure = ((branch_payload.get('stages') or {}).get('failure_rate') or {})
    existing = {
        row.stage_name: row
        for row in PipelineBranchStageKpi.query.filter_by(branch_id=branch_row.id).all()
    }

    for stage_name, row in list(existing.items()):
        if stage_name not in stage_failure:
            db.session.delete(row)

    for stage_name, failure_rate in stage_failure.items():
        clean_name = (stage_name or '').strip()
        if not clean_name:
            continue

        row = existing.get(clean_name)
        if row is None:
            row = PipelineBranchStageKpi(
                branch_id=branch_row.id,
                stage_name=clean_name,
            )
            db.session.add(row)

        row.failure_rate = failure_rate


def _sync_build_stages(build_row, stages):
    existing = {
        row.stage_name: row
        for row in PipelineBranchBuildStage.query.filter_by(
            pipeline_branch_build_id=build_row.id
        ).all()
    }
    incoming_names = set()

    for stage in stages or []:
        stage_name = (stage.get('name') or '').strip()
        if not stage_name:
            continue

        incoming_names.add(stage_name)
        row = existing.get(stage_name)
        if row is None:
            row = PipelineBranchBuildStage(
                pipeline_branch_build_id=build_row.id,
                stage_name=stage_name,
            )
            db.session.add(row)

        row.status = stage.get('status')
        row.started_at = _millis_to_datetime(stage.get('start_time'))
        row.duration_ms = int(stage.get('duration_ms') or 0)

    for stage_name, row in existing.items():
        if stage_name not in incoming_names:
            db.session.delete(row)


def _branches_payload_for_storage(branches_payload, selected_branch):
    payload = dict(branches_payload or {})
    if not payload or not _store_selected_branch_only():
        return payload

    clean_selected_branch = (selected_branch or '').strip()
    if clean_selected_branch and clean_selected_branch in payload:
        return {
            clean_selected_branch: payload[clean_selected_branch],
        }

    return payload


def _sync_branch_builds(branch_row, branch_payload):
    prepared = _prepare_branch_build_payloads(branch_payload)

    PipelineBranchBuild.query.filter_by(branch_id=branch_row.id).update(
        {
            PipelineBranchBuild.is_last_build: False,
            PipelineBranchBuild.is_last_completed_build: False,
        },
        synchronize_session=False,
    )

    build_numbers = list(prepared.keys())
    existing = {
        row.build_number: row
        for row in PipelineBranchBuild.query.filter_by(branch_id=branch_row.id).all()
    }

    for build_number, row in list(existing.items()):
        if build_number in prepared:
            continue
        db.session.delete(row)
        existing.pop(build_number, None)

    for build_number, payload in prepared.items():
        row = existing.get(build_number)
        if row is None:
            row = PipelineBranchBuild(
                branch_id=branch_row.id,
                build_number=build_number,
            )
            db.session.add(row)

        row.result = payload.get('result')
        row.is_running = payload.get('result') is None
        row.is_last_build = bool(payload.get('is_last_build'))
        row.is_last_completed_build = bool(payload.get('is_last_completed_build'))
        row.timestamp_ms = int(payload.get('timestamp_ms') or 0)
        row.started_at = _millis_to_datetime(payload.get('timestamp_ms'))
        row.duration_seconds = int(payload.get('duration_seconds') or 0)
        row.duration_ms = int(payload.get('duration_ms') or (row.duration_seconds * 1000))
        _apply_optional_build_quality_fields(row, payload)

        db.session.flush()
        if payload.get('stages') is not None:
            _sync_build_stages(row, payload.get('stages') or [])


def sync_pipeline_snapshot(payload):
    if not payload or not payload.get('connected'):
        return False

    pipeline_payload = payload.get('pipeline') or {}
    branches_payload = payload.get('branches') or {}
    if not branches_payload:
        return False

    now = _utcnow()
    selected_branch = pipeline_payload.get('selected_branch') or _selected_branch_from_config()
    branches_payload = _branches_payload_for_storage(branches_payload, selected_branch)
    job_path = _pipeline_job_path_from_config()

    try:
        pipeline_row = PipelineDefinition.query.filter_by(
            source_system='jenkins',
            job_path=job_path,
        ).one_or_none()
        if pipeline_row is None:
            pipeline_row = PipelineDefinition(
                source_system='jenkins',
                job_path=job_path,
            )
            db.session.add(pipeline_row)

        pipeline_row.name = pipeline_payload.get('name') or _pipeline_name_from_job_path(job_path)
        pipeline_row.pipeline_type = pipeline_payload.get('type')
        pipeline_row.selected_branch = selected_branch
        pipeline_row.last_synced_at = now
        db.session.flush()

        existing_branches = {
            row.name: row
            for row in PipelineBranch.query.filter_by(pipeline_id=pipeline_row.id).all()
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
                row = PipelineBranch(
                    pipeline_id=pipeline_row.id,
                    name=branch_name,
                )
                db.session.add(row)

            summary = branch_payload.get('summary') or {}
            status = branch_payload.get('status') or {}
            links = branch_payload.get('links') or {}
            quality = branch_payload.get('quality') or {}
            deployment = ((branch_payload.get('deployment') or {}).get('frequency') or {})
            last_completed_build = branch_payload.get('last_completed_build') or {}

            row.job_name = branch_payload.get('job_name')
            row.is_selected = branch_name == selected_branch
            row.job_url = links.get('job_url')
            row.status_color = status.get('color')
            row.is_building = bool(status.get('building'))
            row.health_score = summary.get('health_score')
            row.last_build_number = (
                summary.get('last_build_number')
                or ((branch_payload.get('last_build') or {}).get('number'))
            )
            row.last_completed_build_number = (
                summary.get('last_completed_build_number')
                or last_completed_build.get('number')
            )
            row.total_builds = summary.get('total_builds')
            row.successful_builds = summary.get('successful')
            row.failed_builds = summary.get('failed')
            row.aborted_builds = summary.get('aborted')
            row.running_builds = summary.get('running')
            row.success_rate = summary.get('success_rate')
            row.avg_duration_ms = summary.get('avg_duration_ms')
            row.avg_duration_seconds = summary.get('avg_duration_seconds')
            row.avg_test_coverage = quality.get('avg_test_coverage')
            row.deployment_successful = deployment.get('successful')
            row.deployment_total = deployment.get('total')
            row.deployment_rate = deployment.get('rate')
            row.last_synced_at = now

            db.session.flush()
            _sync_branch_stage_kpis(row, branch_payload)
            _sync_branch_builds(row, branch_payload)

        db.session.commit()
        return True
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to sync structured pipeline snapshot to the database.'
        )
        return False


def _load_branch_build_rows(branch_id, build_numbers):
    if not build_numbers:
        return {}

    rows = PipelineBranchBuild.query.filter(
        PipelineBranchBuild.branch_id == branch_id,
        PipelineBranchBuild.build_number.in_(build_numbers),
    ).all()
    return {row.build_number: row for row in rows}


def _build_needs_quality_backfill(build, row):
    if row is None:
        return False

    if _build_result_from_payload(build) is None:
        return False

    if not _build_runs_test_related_stages(build):
        return False

    has_coverage = row.coverage_percent is not None
    has_junit = all(
        value is not None
        for value in (
            row.junit_total,
            row.junit_passed,
            row.junit_failed,
            row.junit_skipped,
        )
    )
    return not has_coverage or not has_junit


def _run_backfill_tasks(build_numbers, fetcher, max_workers=6, timeout=120):
    if not build_numbers:
        return {}

    results = {}
    for chunk in _chunked(build_numbers, PIPELINE_TEST_HISTORY_BACKFILL_BATCH_SIZE):
        tasks = {
            number: (lambda n=number: fetcher(n))
            for number in chunk
        }
        try:
            results.update(
                parallel_execute(
                    tasks,
                    max_workers=max_workers,
                    timeout=timeout,
                )
            )
        except Exception:
            current_app.logger.exception(
                'Failed to backfill Jenkins test history for build batch %s.',
                chunk,
            )
    return results


def backfill_branch_test_results(
    branch_name,
    builds,
    coverage_fetcher,
    test_report_fetcher,
):
    pipeline = _load_pipeline_definition()
    if pipeline is None:
        return {
            'candidate_builds': 0,
            'coverage_updates': 0,
            'junit_updates': 0,
        }

    branch_row = PipelineBranch.query.filter_by(
        pipeline_id=pipeline.id,
        name=branch_name,
    ).one_or_none()
    if branch_row is None:
        return {
            'candidate_builds': 0,
            'coverage_updates': 0,
            'junit_updates': 0,
        }

    candidate_numbers = []
    for build in builds or []:
        build_number = _build_number_from_payload(build)
        if build_number is None:
            continue
        candidate_numbers.append(build_number)

    existing_rows = _load_branch_build_rows(branch_row.id, candidate_numbers)
    coverage_numbers = []
    junit_numbers = []

    for build in builds or []:
        build_number = _build_number_from_payload(build)
        if build_number is None:
            continue

        row = existing_rows.get(build_number)
        if not _build_needs_quality_backfill(build, row):
            continue

        if row.coverage_percent is None:
            coverage_numbers.append(build_number)
        if any(
            value is None
            for value in (
                row.junit_total,
                row.junit_passed,
                row.junit_failed,
                row.junit_skipped,
            )
        ):
            junit_numbers.append(build_number)

    coverage_results = _run_backfill_tasks(coverage_numbers, coverage_fetcher)
    junit_results = _run_backfill_tasks(junit_numbers, test_report_fetcher)

    coverage_updates = 0
    junit_updates = 0

    try:
        for build_number, coverage in coverage_results.items():
            if coverage is None:
                continue
            row = existing_rows.get(build_number)
            if row is None:
                continue
            row.coverage_percent = coverage
            coverage_updates += 1

        for build_number, report in junit_results.items():
            if not report:
                continue
            row = existing_rows.get(build_number)
            if row is None:
                continue
            row.junit_total = report.get('total')
            row.junit_passed = report.get('passed')
            row.junit_failed = report.get('failed')
            row.junit_skipped = report.get('skipped')
            junit_updates += 1

        if coverage_updates or junit_updates:
            db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to backfill historical test results from Jenkins.'
        )
        coverage_updates = 0
        junit_updates = 0

    return {
        'candidate_builds': len(set(coverage_numbers) | set(junit_numbers)),
        'coverage_updates': coverage_updates,
        'junit_updates': junit_updates,
    }


def _load_existing_builds(build_numbers):
    if not build_numbers:
        return {}

    rows = PipelineBuildDuration.query.filter(
        PipelineBuildDuration.build_number.in_(build_numbers)
    ).all()
    return {row.build_number: row for row in rows}


def _load_existing_stages(build_numbers):
    if not build_numbers:
        return {}

    rows = (
        PipelineStageDuration.query
        .join(PipelineBuildDuration)
        .filter(PipelineBuildDuration.build_number.in_(build_numbers))
        .all()
    )
    return {(row.pipeline_build_id, row.stage_name): row for row in rows}


def sync_pipeline_durations(builds):
    build_numbers = [b.get('number') for b in builds if b.get('number') is not None]
    if not build_numbers:
        return

    try:
        existing_builds = _load_existing_builds(build_numbers)

        for build in builds:
            build_number = build.get('number')
            if build_number is None:
                continue

            row = existing_builds.get(build_number)
            if row is None:
                row = PipelineBuildDuration(build_number=build_number)
                db.session.add(row)
                existing_builds[build_number] = row

            duration_seconds = int(build.get('duration') or build.get('duration_seconds') or 0)
            duration_ms = int(build.get('duration_ms') or (duration_seconds * 1000))
            row.result = build.get('result')
            row.started_at = _millis_to_datetime(build.get('timestamp'))
            row.duration_seconds = duration_seconds
            row.duration_ms = duration_ms

        db.session.flush()

        existing_stages = _load_existing_stages(build_numbers)
        for build in builds:
            build_number = build.get('number')
            if build_number is None:
                continue

            build_row = existing_builds[build_number]
            for stage in build.get('stages', []):
                stage_name = (stage.get('name') or '').strip()
                if not stage_name:
                    continue

                key = (build_row.id, stage_name)
                stage_row = existing_stages.get(key)
                if stage_row is None:
                    stage_row = PipelineStageDuration(
                        pipeline_build_id=build_row.id,
                        stage_name=stage_name,
                    )
                    db.session.add(stage_row)
                    existing_stages[key] = stage_row

                stage_row.status = stage.get('status')
                stage_row.started_at = _millis_to_datetime(stage.get('start_time'))
                stage_row.duration_ms = int(stage.get('duration_ms') or 0)

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to sync pipeline and stage durations to the database.'
        )
