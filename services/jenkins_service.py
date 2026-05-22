from datetime import datetime, timedelta, timezone
import threading

from collectors.jenkins_collector import (
    get_all_builds,
    get_branch_jobs,
    get_selected_branch_build_head,
    get_health_score,
    get_stages,
    get_coverage_percent,
    get_test_report,
)
from flask import current_app
from extensions import cache
from pipeline_identity import (
    configured_branch_name,
    configured_pipeline_job_path,
    pipeline_name,
)
from services.parallel_executor import parallel_execute
from services.pipeline_storage_service import (
    backfill_branch_test_results,
    build_tests_duration_points,
    get_overview_kpis_from_stored_pipeline,
    get_stored_pipeline_kpis,
    get_stored_overview_kpis,
    sync_pipeline_snapshot,
    warm_pipeline_snapshot_cache,
)

DEPLOY_STAGE = 'Deploy to AKS'
ROLLOUT_STAGE = 'Wait for AKS Rollout'
PIPELINE_HEAD_CACHE_MAX_AGE_SECONDS = 2
LIVE_RUNNING_BUILDS_CACHE_VERSION = 'v1'
LIVE_RUNNING_BUILDS_CACHE_TIMEOUT_SECONDS = 1
PIPELINE_COVERAGE_TREND_HISTORY_LIMIT = 120
PIPELINE_JUNIT_TREND_HISTORY_LIMIT = 20
PIPELINE_JUNIT_TREND_LOOKBACK_DAYS = 28

_pipeline_head_cache = {
    'checked_at': None,
    'head': None,
}
_pipeline_head_cache_lock = threading.Lock()
_pipeline_refresh_lock = threading.Lock()
_pipeline_refresh_in_progress = False


def _map_stage_statuses(stages):
    return {
        (s.get('name') or '').strip(): (s.get('status') or '').strip().upper()
        for s in (stages or [])
    }


def _call_in_app_context(app, func):
    with app.app_context():
        return func()


def _select_recent_junit_builds(finished_builds):
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=PIPELINE_JUNIT_TREND_LOOKBACK_DAYS)).timestamp() * 1000
    )
    recent_builds = [
        build for build in (finished_builds or [])
        if (build.get('timestamp', 0) or 0) >= cutoff_ms
    ]
    if recent_builds:
        return list(reversed(recent_builds))
    return list(reversed((finished_builds or [])[:PIPELINE_JUNIT_TREND_HISTORY_LIMIT]))


def _live_running_builds_cache_key(include_stages=True):
    variant = 'stages' if include_stages else 'summary'
    return (
        f'pipeline_live_running:{LIVE_RUNNING_BUILDS_CACHE_VERSION}:'
        f'{variant}:'
        f'{configured_pipeline_job_path(current_app.config, default_branch="main")}:'
        f'{configured_branch_name(current_app.config, default="main")}'
    )


def _snapshot_is_stale(timestamp, max_age_seconds=PIPELINE_HEAD_CACHE_MAX_AGE_SECONDS, now=None):
    if timestamp is None:
        return True

    snapshot_time = timestamp
    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)

    current_time = now or datetime.now(timezone.utc)
    return current_time - snapshot_time >= timedelta(seconds=max_age_seconds)


def _get_selected_branch_snapshot(payload):
    pipeline = payload.get('pipeline') or {}
    selected_branch = pipeline.get('selected_branch')
    branches = payload.get('branches') or {}
    return selected_branch, (branches.get(selected_branch) or {})


def _build_snapshot_key(build):
    build = build or {}
    return (
        build.get('number'),
        build.get('result'),
        build.get('timestamp'),
    )


def _selected_branch_head_changed(stored_payload, live_head):
    if not stored_payload:
        return True
    if not live_head:
        return False

    _, branch_payload = _get_selected_branch_snapshot(stored_payload)
    if not branch_payload:
        return True

    stored_last_build = branch_payload.get('last_build') or {}
    stored_last_completed = branch_payload.get('last_completed_build') or {}

    return (
        _build_snapshot_key(stored_last_build) != _build_snapshot_key(live_head.get('last_build'))
        or _build_snapshot_key(stored_last_completed)
        != _build_snapshot_key(live_head.get('last_completed_build'))
    )


def _find_build_in_branch_payload(branch_payload, build_number):
    if build_number is None:
        return None

    return next(
        (
            build
            for build in (branch_payload.get('builds') or [])
            if build.get('number') == build_number
        ),
        None,
    )


def _find_branch_trend_point(branch_payload, trend_name, build_number):
    if build_number is None:
        return None

    trends = branch_payload.get('trends') or {}
    return next(
        (
            item
            for item in (trends.get(trend_name) or [])
            if item.get('number') == build_number
        ),
        None,
    )


def _build_has_test_related_stages(build_payload):
    for stage in build_payload.get('stages') or []:
        stage_name = (stage.get('name') or '').strip().lower()
        if any(marker in stage_name for marker in ('pytest', 'test', 'pylint', 'sonar')):
            return True
    return False


def _build_needs_artifact_refresh(branch_payload, build_number):
    build_payload = _find_build_in_branch_payload(branch_payload, build_number)
    if not build_payload:
        return False

    if build_payload.get('result') is None:
        return not bool(build_payload.get('stages'))

    if not _build_has_test_related_stages(build_payload):
        return False

    has_coverage = build_payload.get('coverage_percent') is not None
    has_junit = any(
        build_payload.get(key) is not None
        for key in ('junit_total', 'junit_passed', 'junit_failed', 'junit_skipped')
    )

    coverage_item = _find_branch_trend_point(branch_payload, 'coverage', build_number) or {}
    junit_item = _find_branch_trend_point(branch_payload, 'junit', build_number) or {}

    has_coverage = has_coverage or coverage_item.get('coverage') is not None
    has_junit = has_junit or any(
        junit_item.get(key) is not None
        for key in ('total', 'passed', 'failed', 'skipped')
    )

    return not has_coverage or not has_junit


def _stored_payload_needs_artifact_refresh(stored_payload):
    if not stored_payload:
        return True

    _, branch_payload = _get_selected_branch_snapshot(stored_payload)
    if not branch_payload:
        return True

    return any(
        _build_needs_artifact_refresh(branch_payload, build.get('number'))
        for build in (branch_payload.get('builds') or [])
        if build.get('number') is not None
    )


def _stored_payload_has_stage_history_gaps(stored_payload):
    if not stored_payload:
        return True

    _, branch_payload = _get_selected_branch_snapshot(stored_payload)
    if not branch_payload:
        return True

    finished_builds = [
        build
        for build in (branch_payload.get('builds') or [])
        if build.get('number') is not None and build.get('result') is not None
    ]
    if not finished_builds:
        return False

    return not any(build.get('stages') for build in finished_builds)


def _stored_branch_payload(payload):
    _, branch_payload = _get_selected_branch_snapshot(payload or {})
    return branch_payload or {}


def _overlay_stored_stage_history(builds_data, stored_branch_payload):
    if not stored_branch_payload:
        return list(builds_data or [])

    stored_builds_by_number = {
        build.get('number'): build
        for build in (stored_branch_payload.get('builds') or [])
        if build.get('number') is not None
    }

    merged = []
    for build in builds_data or []:
        stored_build = stored_builds_by_number.get(build.get('number')) or {}
        merged.append({
            **stored_build,
            **build,
            'stages': (
                build.get('stages')
                if build.get('stages') is not None
                else (stored_build.get('stages') or [])
            ),
        })

    return merged


def _get_cached_selected_branch_head():
    now = datetime.now(timezone.utc)
    with _pipeline_head_cache_lock:
        checked_at = _pipeline_head_cache.get('checked_at')
        cached_head = _pipeline_head_cache.get('head')
        if (
            checked_at is not None
            and not _snapshot_is_stale(checked_at, max_age_seconds=PIPELINE_HEAD_CACHE_MAX_AGE_SECONDS, now=now)
        ):
            return cached_head

    live_head = get_selected_branch_build_head()

    with _pipeline_head_cache_lock:
        _pipeline_head_cache['checked_at'] = now
        _pipeline_head_cache['head'] = live_head

    return live_head


def invalidate_pipeline_head_cache():
    with _pipeline_head_cache_lock:
        _pipeline_head_cache['checked_at'] = None
        _pipeline_head_cache['head'] = None


def invalidate_live_running_builds_cache():
    cache.delete(_live_running_builds_cache_key(include_stages=True))
    cache.delete(_live_running_builds_cache_key(include_stages=False))


def invalidate_pipeline_live_state():
    invalidate_live_running_builds_cache()
    invalidate_pipeline_head_cache()


def _cache_selected_branch_head(payload):
    selected_branch, branch_payload = _get_selected_branch_snapshot(payload)
    if not selected_branch or not branch_payload:
        return

    with _pipeline_head_cache_lock:
        _pipeline_head_cache['checked_at'] = datetime.now(timezone.utc)
        _pipeline_head_cache['head'] = {
            'name': selected_branch,
            'color': (branch_payload.get('status') or {}).get('color'),
            'health_score': (branch_payload.get('summary') or {}).get('health_score'),
            'last_build': branch_payload.get('last_build'),
            'last_completed_build': branch_payload.get('last_completed_build'),
        }


def get_live_running_builds(include_stages=True):
    cached = cache.get(_live_running_builds_cache_key(include_stages=include_stages))
    if cached is not None:
        return cached

    all_builds = get_all_builds()
    if all_builds is None:
        return []

    running_builds = [
        build
        for build in all_builds
        if build.get('number') is not None and build.get('result') is None
    ]
    if not running_builds:
        payload = []
        cache.set(
            _live_running_builds_cache_key(include_stages=include_stages),
            payload,
            timeout=LIVE_RUNNING_BUILDS_CACHE_TIMEOUT_SECONDS,
        )
        return payload

    stages_by_build = {}
    if include_stages:
        app = current_app._get_current_object()
        stage_tasks = {
            build.get('number'): (
                lambda n=build.get('number'): _call_in_app_context(app, lambda: get_stages(n))
            )
            for build in running_builds
        }
        stages_by_build = (
            parallel_execute(stage_tasks, max_workers=4, timeout=12)
            if stage_tasks
            else {}
        )

    payload = sorted(
        [
            {
                'number': build.get('number'),
                'status': build.get('status'),
                'result': None,
                'timestamp': build.get('timestamp', 0),
                'duration_ms': build.get('duration', 0) or 0,
                'duration_seconds': int((build.get('duration', 0) or 0) / 1000),
                'stages': stages_by_build.get(build.get('number'), []),
            }
            for build in running_builds
        ],
        key=lambda item: (item.get('timestamp', 0), item.get('number', 0)),
        reverse=True,
    )
    cache.set(
        _live_running_builds_cache_key(include_stages=include_stages),
        payload,
        timeout=LIVE_RUNNING_BUILDS_CACHE_TIMEOUT_SECONDS,
    )
    return payload


def _refresh_pipeline_storage_in_background(app, stored_payload=None):
    global _pipeline_refresh_in_progress

    try:
        with app.app_context():
            if stored_payload is not None:
                live_head = _get_cached_selected_branch_head()
                if (
                    not _selected_branch_head_changed(stored_payload, live_head)
                    and not _stored_payload_needs_artifact_refresh(stored_payload)
                ):
                    return

            refresh_pipeline_storage_from_jenkins()
    except Exception:
        app.logger.exception('Background pipeline refresh failed.')
    finally:
        with _pipeline_refresh_lock:
            _pipeline_refresh_in_progress = False


def _start_background_pipeline_refresh(stored_payload=None):
    global _pipeline_refresh_in_progress

    with _pipeline_refresh_lock:
        if _pipeline_refresh_in_progress:
            return False
        _pipeline_refresh_in_progress = True

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_refresh_pipeline_storage_in_background,
        args=(app, stored_payload),
        daemon=True,
        name='pipeline-refresh',
    )
    thread.start()
    return True


def request_pipeline_background_refresh(stored_payload=None):
    return _start_background_pipeline_refresh(stored_payload=stored_payload)


def _schedule_background_refresh_if_needed(stored_payload):
    with _pipeline_head_cache_lock:
        checked_at = _pipeline_head_cache.get('checked_at')

    if _stored_payload_needs_artifact_refresh(stored_payload):
        return _start_background_pipeline_refresh(stored_payload=stored_payload)

    if not _snapshot_is_stale(checked_at, max_age_seconds=PIPELINE_HEAD_CACHE_MAX_AGE_SECONDS):
        return False

    return _start_background_pipeline_refresh(stored_payload=stored_payload)


def _build_summary_payload(build, branch_name):
    duration_seconds = build.get('duration_seconds')
    if duration_seconds is None:
        duration_seconds = build.get('duration')

    duration_ms = build.get('duration_ms')
    if duration_ms is None and duration_seconds is not None:
        duration_ms = int(duration_seconds or 0) * 1000
    if duration_ms is None:
        duration_ms = 0

    if duration_seconds is None:
        duration_seconds = duration_ms // 1000 if duration_ms else 0

    return {
        'branch': branch_name,
        'number': build.get('number'),
        'status': build.get('status'),
        'result': build.get('result'),
        'duration_seconds': duration_seconds,
        'duration_ms': duration_ms,
        'timestamp': build.get('timestamp', 0),
    }


def _build_detail_payload(build, branch_name):
    payload = _build_summary_payload(build, branch_name)
    payload['stages'] = build.get('stages', [])
    return payload


def _branch_status_payload(color):
    return {
        'color': color,
        'building': 'anime' in (color or ''),
    }


def _build_selected_branch_payload(
    branch_name,
    summary,
    health_score,
    builds_data,
    avg_duration,
    failure_rate_by_stage,
    coverage_trend,
    junit_trend,
    tests_duration_trend,
    avg_test_coverage,
    deployment_frequency,
):
    finished = [b for b in builds_data if b.get('result') is not None]
    detailed_builds = [_build_detail_payload(b, branch_name) for b in builds_data]
    trend_builds = [_build_summary_payload(b, branch_name) for b in builds_data]
    return {
        'name': branch_name,
        'selected': True,
        'summary': {
            'last_build_number': summary['last_build_number'],
            'total_builds': summary['total_builds'],
            'successful': summary['successful'],
            'failed': summary['failed'],
            'aborted': summary['aborted'],
            'running': summary['running'],
            'success_rate': summary['success_rate'],
            'health_score': health_score,
            'avg_duration_ms': summary['avg_duration_ms'],
            'avg_duration_seconds': avg_duration,
        },
        'status': _branch_status_payload(None),
        'last_build': _build_summary_payload(builds_data[0], branch_name) if builds_data else None,
        'last_completed_build': (
            _build_summary_payload(finished[0], branch_name) if finished else None
        ),
        'builds': detailed_builds,
        'trends': {
            'builds': trend_builds,
            'durations': [
                {
                    'branch': branch_name,
                    'number': b.get('number'),
                    'duration_seconds': b.get('duration', 0),
                    'duration_ms': b.get('duration_ms', 0),
                }
                for b in finished[-20:]
            ],
            'coverage': coverage_trend,
            'junit': junit_trend,
            'tests_duration': tests_duration_trend,
        },
        'stages': {
            'failure_rate': failure_rate_by_stage,
        },
        'quality': {
            'avg_test_coverage': avg_test_coverage,
        },
        'deployment': {
            'frequency': deployment_frequency,
        },
    }


def _build_branch_overview_payload(branch, selected_branch_name):
    name = branch.get('name')
    return {
        'name': name,
        'selected': name == selected_branch_name,
        'summary': {
            'health_score': branch.get('health_score', 0),
            'last_build_number': (branch.get('last_build') or {}).get('number'),
            'last_completed_build_number': (
                (branch.get('last_completed_build') or {}).get('number')
            ),
        },
        'status': _branch_status_payload(branch.get('color')),
        'last_build': branch.get('last_build'),
        'last_completed_build': branch.get('last_completed_build'),
        'links': {
            'job_url': branch.get('url'),
        },
    }


def _summarize_build_history(all_builds):
    last_build_number = all_builds[0].get('number') if all_builds else None

    finished = [build for build in all_builds if build.get('result') is not None]
    running_lst = [build for build in all_builds if build.get('result') is None]

    successful = sum(1 for b in finished if b.get('result') == 'SUCCESS')
    failed = sum(1 for b in finished if b.get('result') == 'FAILURE')
    aborted = sum(1 for b in finished if b.get('result') == 'ABORTED')

    finished_count = successful + failed + aborted
    rate = round((successful / finished_count * 100), 1) if finished_count > 0 else 0

    durations = [b.get('duration', 0) for b in finished if b.get('duration', 0) > 0]
    avg_duration_ms = int(sum(durations) / len(durations)) if durations else 60000

    return {
        'last_build_number': last_build_number,
        'finished_builds': finished,
        'running_builds': running_lst,
        'total_builds': len(finished),
        'successful': successful,
        'failed': failed,
        'aborted': aborted,
        'running': len(running_lst),
        'success_rate': rate,
        'avg_duration_ms': avg_duration_ms,
        'build_trend': running_lst + finished,
    }


def _fetch_overview_kpis_from_jenkins():
    all_builds = get_all_builds()
    if all_builds is None:
        return {'connected': False}

    summary = _summarize_build_history(all_builds)

    return {
        'connected': True,
        'last_build_number': summary['last_build_number'],
        'total_builds': summary['total_builds'],
        'successful': summary['successful'],
        'failed': summary['failed'],
        'aborted': summary['aborted'],
        'running': summary['running'],
        'success_rate': summary['success_rate'],
        'health_score': get_health_score(),
        'build_trend': summary['build_trend'],
        'avg_duration_ms': summary['avg_duration_ms'],
    }


def _fetch_pipeline_kpis_from_jenkins(include_quality_metrics=True):
    selected_branch = configured_branch_name(current_app.config, default='main')
    all_builds = get_all_builds()
    if all_builds is None:
        return {'connected': False}

    summary = _summarize_build_history(all_builds)
    app = current_app._get_current_object()
    stored_payload = get_stored_pipeline_kpis() if not include_quality_metrics else None
    stored_branch_payload = _stored_branch_payload(stored_payload)

    stage_build_numbers = []
    recent_finished_stage_fetches = 0
    for build in all_builds:
        build_number = build.get('number')
        if build_number is None:
            continue
        if include_quality_metrics:
            stage_build_numbers.append(build_number)
            continue
        if build.get('result') is None:
            stage_build_numbers.append(build_number)
            continue
        if recent_finished_stage_fetches < 5:
            stage_build_numbers.append(build_number)
            recent_finished_stage_fetches += 1

    stage_tasks = {
        build_number: (
            lambda n=build_number: _call_in_app_context(app, lambda: get_stages(n))
        )
        for build_number in stage_build_numbers
    }
    stages_by_build = (
        parallel_execute(stage_tasks, max_workers=6, timeout=20)
        if stage_tasks
        else {}
    )

    builds_data = []
    for b in all_builds:
        num = b.get('number')
        stages = stages_by_build.get(num) if num else None
        builds_data.append({
            'branch': selected_branch,
            'number': num,
            'status': b.get('status'),
            'result': b.get('result'),
            'duration': b.get('duration', 0) // 1000 if b.get('duration') else 0,
            'duration_ms': b.get('duration', 0) or 0,
            'timestamp': b.get('timestamp', 0),
            'stages': stages,
        })

    history_builds_data = (
        _overlay_stored_stage_history(builds_data, stored_branch_payload)
        if not include_quality_metrics
        else builds_data
    )
    finished = [b for b in history_builds_data if b['result'] is not None]
    tests_duration_trend = build_tests_duration_points(
        history_builds_data,
        branch_name=selected_branch,
        finished_only=True,
    )

    durations = [b['duration'] for b in finished if b['duration'] > 0]
    avg_duration = round(sum(durations) / len(durations)) if durations else 0

    stage_failures = {}
    stage_totals = {}
    for b in finished:
        for stage in (b.get('stages') or []):
            stage_name = stage.get('name', 'Unknown')
            stage_totals[stage_name] = stage_totals.get(stage_name, 0) + 1
            if stage.get('status') == 'FAILED':
                stage_failures[stage_name] = stage_failures.get(stage_name, 0) + 1

    failure_rate_by_stage = {}
    for stage_name, count in stage_totals.items():
        failures = stage_failures.get(stage_name, 0)
        failure_rate_by_stage[stage_name] = round((failures / count * 100), 1) if count > 0 else 0

    coverage_trend = []
    junit_trend = []
    avg_test_coverage = None
    if include_quality_metrics:
        coverage_builds = list(reversed(finished[:PIPELINE_COVERAGE_TREND_HISTORY_LIMIT]))
        junit_builds = _select_recent_junit_builds(finished)

        coverage_tasks = {
            b.get('number'): (
                lambda n=b.get('number'): _call_in_app_context(app, lambda: get_coverage_percent(n))
            )
            for b in coverage_builds
            if b.get('number')
        }
        test_report_tasks = {
            b.get('number'): (
                lambda n=b.get('number'): _call_in_app_context(app, lambda: get_test_report(n))
            )
            for b in junit_builds
            if b.get('number')
        }

        coverage_by_build = (
            parallel_execute(coverage_tasks, max_workers=6, timeout=20)
            if coverage_tasks
            else {}
        )
        test_reports_by_build = (
            parallel_execute(test_report_tasks, max_workers=6, timeout=20)
            if test_report_tasks
            else {}
        )

        coverage_vals = []
        for b in coverage_builds:
            num = b.get('number')
            coverage = coverage_by_build.get(num) if num else None
            if coverage is not None:
                coverage_vals.append(coverage)
            coverage_trend.append({
                'branch': selected_branch,
                'number': num,
                'coverage': coverage,
                'timestamp': b.get('timestamp', 0),
            })

        for b in junit_builds:
            num = b.get('number')
            report = test_reports_by_build.get(num) if num else None
            if report:
                junit_trend.append({
                    'branch': selected_branch,
                    'number': num,
                    'timestamp': b.get('timestamp', 0),
                    **report,
                })
            else:
                junit_trend.append({
                    'branch': selected_branch,
                    'number': num,
                    'timestamp': b.get('timestamp', 0),
                    'total': None,
                    'passed': None,
                    'failed': None,
                    'skipped': None,
                })

        avg_test_coverage = round(sum(coverage_vals) / len(coverage_vals), 1) if coverage_vals else None
    else:
        stored_trends = stored_branch_payload.get('trends') or {}
        stored_quality = stored_branch_payload.get('quality') or {}
        coverage_trend = list(stored_trends.get('coverage') or [])
        junit_trend = list(stored_trends.get('junit') or [])
        avg_test_coverage = stored_quality.get('avg_test_coverage')

    successful_deployments = 0
    total_finished_builds = len(finished)

    for b in finished:
        stage_map = _map_stage_statuses(b.get('stages') or [])
        deploy_ok = stage_map.get(DEPLOY_STAGE) == 'SUCCESS'
        rollout_ok = stage_map.get(ROLLOUT_STAGE) == 'SUCCESS'

        if deploy_ok and rollout_ok:
            successful_deployments += 1

    deployment_rate = round(
        (successful_deployments / total_finished_builds) * 100, 1
    ) if total_finished_builds > 0 else 0

    health_score = get_health_score()
    deployment_frequency = {
        'successful': successful_deployments,
        'total': total_finished_builds,
        'rate': deployment_rate,
    }

    current_branch_payload = _build_selected_branch_payload(
        branch_name=selected_branch,
        summary=summary,
        health_score=health_score,
        builds_data=builds_data,
        avg_duration=avg_duration,
        failure_rate_by_stage=failure_rate_by_stage,
        coverage_trend=coverage_trend,
        junit_trend=junit_trend,
        tests_duration_trend=tests_duration_trend,
        avg_test_coverage=avg_test_coverage,
        deployment_frequency=deployment_frequency,
    )

    branches = {selected_branch: current_branch_payload}
    branch_jobs = get_branch_jobs()
    if branch_jobs:
        ordered_branches = {}
        for branch in sorted(
            branch_jobs,
            key=lambda item: (
                item.get('name') != selected_branch,
                (item.get('name') or '').lower(),
            ),
        ):
            name = branch.get('name')
            if not name:
                continue
            ordered_branches[name] = _build_branch_overview_payload(branch, selected_branch)

        selected_overview = ordered_branches.get(selected_branch, {})
        ordered_branches[selected_branch] = {
            **selected_overview,
            **current_branch_payload,
            'summary': {
                **(selected_overview.get('summary') or {}),
                **(current_branch_payload.get('summary') or {}),
            },
            'status': selected_overview.get('status') or current_branch_payload.get('status'),
            'last_build': (
                current_branch_payload.get('last_build')
                or selected_overview.get('last_build')
            ),
            'last_completed_build': (
                current_branch_payload.get('last_completed_build')
                or selected_overview.get('last_completed_build')
            ),
            'links': selected_overview.get('links', {}),
        }
        branches = ordered_branches

    return {
        'connected': True,
        'pipeline': {
            'name': pipeline_name(
                current_app.config.get('JENKINS_JOB'),
                branch_name=selected_branch,
            ),
            'type': 'multibranch' if branch_jobs else 'single-branch',
            'selected_branch': selected_branch,
        },
        'branches': branches,
    }


def refresh_pipeline_storage_from_jenkins(
    *,
    include_quality_metrics=True,
    include_quality_backfill=True,
):
    if not include_quality_metrics:
        stored_payload = get_stored_pipeline_kpis()
        if _stored_payload_has_stage_history_gaps(stored_payload):
            include_quality_metrics = True

    payload = _fetch_pipeline_kpis_from_jenkins(
        include_quality_metrics=include_quality_metrics,
    )
    if not payload.get('connected'):
        return payload

    sync_pipeline_snapshot(payload)
    selected_branch = (payload.get('pipeline') or {}).get('selected_branch')
    selected_payload = ((payload.get('branches') or {}).get(selected_branch) or {})
    if include_quality_backfill:
        app = current_app._get_current_object()
        backfill_branch_test_results(
            selected_branch,
            selected_payload.get('builds') or [],
            coverage_fetcher=lambda n: _call_in_app_context(app, lambda: get_coverage_percent(n)),
            test_report_fetcher=lambda n: _call_in_app_context(app, lambda: get_test_report(n)),
        )

    warm_pipeline_snapshot_cache()
    _cache_selected_branch_head(payload)
    return payload


def get_overview_kpis():
    stored_pipeline = get_stored_pipeline_kpis()
    if stored_pipeline:
        _schedule_background_refresh_if_needed(stored_pipeline)
        stored_overview = get_stored_overview_kpis()
        if stored_overview:
            return stored_overview
        overview = get_overview_kpis_from_stored_pipeline(stored_pipeline)
        if overview:
            return overview

    live = refresh_pipeline_storage_from_jenkins()
    if live.get('connected'):
        stored_pipeline = get_stored_pipeline_kpis()
        stored_overview = get_stored_overview_kpis()
        if stored_overview:
            return stored_overview
        overview = get_overview_kpis_from_stored_pipeline(live)
        if overview:
            return overview

    return _fetch_overview_kpis_from_jenkins()


def get_pipeline_kpis():
    stored = get_stored_pipeline_kpis()
    if stored:
        _schedule_background_refresh_if_needed(stored)
        return stored

    live = refresh_pipeline_storage_from_jenkins()
    if live.get('connected'):
        stored = get_stored_pipeline_kpis()
        if stored:
            return stored

    return stored or live
