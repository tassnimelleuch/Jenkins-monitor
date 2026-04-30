from collectors.jenkins_collectors import (
    get_all_builds,
    get_last_n_finished,
    get_running_builds,
    get_health_score,
    get_stages,
    get_coverage_percent,
    get_test_report,
)
from flask import current_app
from services.parallel_executor import parallel_execute

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
            'number': num,
            'result': b.get('result'),
            'duration': b.get('duration', 0) // 1000 if b.get('duration') else 0,
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
            'number': num,
            'coverage': coverage,
        })

        report = test_reports_by_build.get(num) if num else None
        if report:
            junit_trend.append({
                'number': num,
                **report,
            })
        else:
            junit_trend.append({
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

    return {
        'connected': True,
        'last_build_number': summary['last_build_number'],
        'total_builds': summary['total_builds'],
        'successful': summary['successful'],
        'failed': summary['failed'],
        'aborted': summary['aborted'],
        'running': summary['running'],
        'success_rate': summary['success_rate'],
        'build_trend': summary['build_trend'],
        'avg_duration_ms': summary['avg_duration_ms'],
        'builds': builds_data,
        'health_score': get_health_score(),
        'avg_duration_seconds': avg_duration,
        'failure_rate_by_stage': failure_rate_by_stage,
        'build_durations': [(b['number'], b['duration']) for b in finished[-20:]],
        'avg_test_coverage': avg_test_coverage,
        'coverage_trend': coverage_trend,
        'junit_trend': junit_trend,
        'deployment_frequency': {
            'successful': successful_deployments,
            'total': total_finished_builds,
            'rate': deployment_rate,
        },
    }
