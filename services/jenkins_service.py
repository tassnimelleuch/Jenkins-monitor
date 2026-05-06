from collectors.jenkins_collector import (
    get_all_builds,
    get_branch_jobs,
    get_last_n_finished,
    get_running_builds,
    get_health_score,
    get_stages,
    get_coverage_percent,
    get_test_report,
)
from flask import current_app
from services.parallel_executor import parallel_execute
from services.pipeline_storage_service import sync_pipeline_durations

DEPLOY_STAGE = 'Deploy to AKS'
ROLLOUT_STAGE = 'Wait for AKS Rollout'


def _stage_status_map(stages):
    return {
        (s.get('name') or '').strip(): (s.get('status') or '').strip().upper()
        for s in (stages or [])
    }


def _run_in_app_context(app, func):
    with app.app_context():
        return func()


def _get_selected_branch_name():
    branch = (current_app.config.get('JENKINS_BRANCH') or 'main').strip().strip('/')
    return branch or 'main'


def _get_pipeline_name(branch_name=None):
    raw_job = (current_app.config.get('JENKINS_JOB') or '').strip().strip('/')
    if not raw_job:
        return 'Jenkins Pipeline'

    normalized = raw_job.replace('/job/', '/')
    if normalized.startswith('job/'):
        normalized = normalized[4:]

    parts = [part for part in normalized.split('/') if part]
    branch = branch_name or _get_selected_branch_name()
    if branch and len(parts) > 1 and parts[-1] == branch:
        parts = parts[:-1]
    return parts[-1] if parts else raw_job


def _serialize_build(build, branch_name):
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
        'result': build.get('result'),
        'duration_seconds': duration_seconds,
        'duration_ms': duration_ms,
        'timestamp': build.get('timestamp', 0),
    }


def _serialize_detailed_build(build, branch_name):
    payload = _serialize_build(build, branch_name)
    payload['stages'] = build.get('stages', [])
    return payload


def _branch_status(color):
    return {
        'color': color,
        'building': 'anime' in (color or ''),
    }


def _selected_branch_payload(
    branch_name,
    summary,
    health_score,
    builds_data,
    avg_duration,
    failure_rate_by_stage,
    coverage_trend,
    junit_trend,
    avg_test_coverage,
    deployment_frequency,
):
    finished = [b for b in builds_data if b.get('result') is not None]
    api_builds = [_serialize_detailed_build(b, branch_name) for b in builds_data]
    trend_builds = [_serialize_build(b, branch_name) for b in builds_data]
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
        'status': _branch_status(None),
        'last_build': _serialize_build(api_builds[0], branch_name) if api_builds else None,
        'last_completed_build': (
            _serialize_build(finished[0], branch_name) if finished else None
        ),
        'builds': api_builds,
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


def _branch_overview_payload(branch, selected_branch_name):
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
        'status': _branch_status(branch.get('color')),
        'last_build': branch.get('last_build'),
        'last_completed_build': branch.get('last_completed_build'),
        'links': {
            'job_url': branch.get('url'),
        },
    }


def _summarize_builds(all_builds):
    last_build_number = all_builds[0].get('number') if all_builds else None

    finished = get_last_n_finished(None, builds=all_builds)
    running_lst = get_running_builds(builds=all_builds)

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


def get_kpis():
    all_builds = get_all_builds()
    if all_builds is None:
        return {'connected': False}

    summary = _summarize_builds(all_builds)

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


def get_pipeline_kpis():
    selected_branch = _get_selected_branch_name()
    all_builds = get_all_builds()
    if all_builds is None:
        return {'connected': False}

    summary = _summarize_builds(all_builds)
    app = current_app._get_current_object()

    stage_tasks = {
        b.get('number'): (
            lambda n=b.get('number'): _run_in_app_context(app, lambda: get_stages(n))
        )
        for b in all_builds
        if b.get('number')
    }
    stages_by_build = (
        parallel_execute(stage_tasks, max_workers=6, timeout=20)
        if stage_tasks
        else {}
    )

    builds_data = []
    for b in all_builds:
        num = b.get('number')
        stages = stages_by_build.get(num, []) if num else []
        builds_data.append({
            'branch': selected_branch,
            'number': num,
            'result': b.get('result'),
            'duration': b.get('duration', 0) // 1000 if b.get('duration') else 0,
            'duration_ms': b.get('duration', 0) or 0,
            'timestamp': b.get('timestamp', 0),
            'stages': stages,
        })

    finished = [b for b in builds_data if b['result'] is not None]

    durations = [b['duration'] for b in finished if b['duration'] > 0]
    avg_duration = round(sum(durations) / len(durations)) if durations else 0

    stage_failures = {}
    stage_totals = {}
    for b in finished:
        for stage in b.get('stages', []):
            stage_name = stage.get('name', 'Unknown')
            stage_totals[stage_name] = stage_totals.get(stage_name, 0) + 1
            if stage.get('status') == 'FAILED':
                stage_failures[stage_name] = stage_failures.get(stage_name, 0) + 1

    failure_rate_by_stage = {}
    for stage_name, count in stage_totals.items():
        failures = stage_failures.get(stage_name, 0)
        failure_rate_by_stage[stage_name] = round((failures / count * 100), 1) if count > 0 else 0

    finished_recent = finished[:20]
    trend_builds = list(reversed(finished_recent))

    coverage_tasks = {
        b.get('number'): (
            lambda n=b.get('number'): _run_in_app_context(app, lambda: get_coverage_percent(n))
        )
        for b in trend_builds
        if b.get('number')
    }
    test_report_tasks = {
        b.get('number'): (
            lambda n=b.get('number'): _run_in_app_context(app, lambda: get_test_report(n))
        )
        for b in trend_builds
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

    coverage_trend = []
    junit_trend = []
    coverage_vals = []
    for b in trend_builds:
        num = b.get('number')
        coverage = coverage_by_build.get(num) if num else None
        if coverage is not None:
            coverage_vals.append(coverage)
        coverage_trend.append({
            'branch': selected_branch,
            'number': num,
            'coverage': coverage,
        })

        report = test_reports_by_build.get(num) if num else None
        if report:
            junit_trend.append({
                'branch': selected_branch,
                'number': num,
                **report,
            })
        else:
            junit_trend.append({
                'branch': selected_branch,
                'number': num,
                'total': None,
                'passed': None,
                'failed': None,
                'skipped': None,
            })

    avg_test_coverage = round(sum(coverage_vals) / len(coverage_vals), 1) if coverage_vals else None

    successful_deployments = 0
    total_finished_builds = len(finished)

    for b in finished:
        stage_map = _stage_status_map(b.get('stages', []))
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

    current_branch_payload = _selected_branch_payload(
        branch_name=selected_branch,
        summary=summary,
        health_score=health_score,
        builds_data=builds_data,
        avg_duration=avg_duration,
        failure_rate_by_stage=failure_rate_by_stage,
        coverage_trend=coverage_trend,
        junit_trend=junit_trend,
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
            ordered_branches[name] = _branch_overview_payload(branch, selected_branch)

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

    sync_pipeline_durations(builds_data)
    return {
        'connected': True,
        'pipeline': {
            'name': _get_pipeline_name(selected_branch),
            'type': 'multibranch' if branch_jobs else 'single-branch',
            'selected_branch': selected_branch,
        },
        'branches': branches,
    }
