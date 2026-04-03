from providers.jenkins import (
    get_all_builds,
    get_last_n_finished,
    get_running_builds,
    get_health_score,
    get_stages,
    get_coverage_percent,
    get_test_report,
)

DEPLOY_STAGE = 'Deploy to AKS'
ROLLOUT_STAGE = 'Wait for AKS Rollout'


def _stage_status_map(stages):
    return {
        (s.get('name') or '').strip(): (s.get('status') or '').strip().upper()
        for s in (stages or [])
    }


def get_kpis():
    all_builds = get_all_builds()
    if all_builds is None:
        return {'connected': False}

    last_build_number = all_builds[0].get('number') if all_builds else None

    finished = get_last_n_finished(10, builds=all_builds)
    running_lst = get_running_builds(builds=all_builds)
    health = get_health_score()

    successful = sum(1 for b in finished if b.get('result') == 'SUCCESS')
    failed = sum(1 for b in finished if b.get('result') == 'FAILURE')
    aborted = sum(1 for b in finished if b.get('result') == 'ABORTED')

    finished_count = successful + failed + aborted
    rate = round((successful / finished_count * 100), 1) if finished_count > 0 else 0

    durations = [b.get('duration', 0) for b in finished if b.get('duration', 0) > 0]
    avg_duration_ms = int(sum(durations) / len(durations)) if durations else 60000

    trend = running_lst + finished

    return {
        'connected': True,
        'last_build_number': last_build_number,
        'total_builds': len(finished),
        'successful': successful,
        'failed': failed,
        'aborted': aborted,
        'running': len(running_lst),
        'success_rate': rate,
        'health_score': health,
        'build_trend': trend,
        'avg_duration_ms': avg_duration_ms,
    }


def get_pipeline_kpis():
    all_builds = get_all_builds()
    if all_builds is None:
        return {'connected': False}

    builds_data = []
    for b in all_builds:
        num = b.get('number')
        stages = get_stages(num) if num else []
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
    coverage_trend = []
    junit_trend = []
    coverage_vals = []
    for b in trend_builds:
        num = b.get('number')
        coverage = get_coverage_percent(num) if num else None
        if coverage is not None:
            coverage_vals.append(coverage)
        coverage_trend.append({
            'number': num,
            'coverage': coverage,
        })

        report = get_test_report(num) if num else None
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
