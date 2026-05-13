from datetime import datetime, timezone

from services.pipeline_storage_service import get_stored_pipeline_kpis


ALERT_RULE_ID = 'build_duration_over_one_minute'
TEST_ALERT_THRESHOLD_MS = 60_000


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


def get_duration_alerts():
    payload = get_stored_pipeline_kpis() or {}
    if not payload:
        return {
            'connected': False,
            'alerts': [],
            'summary': {
                'alert_count': 0,
                'running_builds': 0,
                'avg_duration_ms': 0,
            },
        }

    pipeline, selected_branch, branch_payload = _selected_branch_payload(payload)
    summary = branch_payload.get('summary') or {}
    builds = branch_payload.get('builds') or []
    avg_duration_ms = int(summary.get('avg_duration_ms') or 0)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

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
            'rule_id': ALERT_RULE_ID,
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
        })

    alerts.sort(key=lambda item: (item.get('exceeded_by_ms') or 0), reverse=True)

    return {
        'connected': True,
        'pipeline': {
            'name': pipeline.get('name') or 'Jenkins Pipeline',
            'selected_branch': selected_branch,
        },
        'rule': {
            'id': ALERT_RULE_ID,
            'name': 'Running build duration over 1 minute',
            'threshold_ms': TEST_ALERT_THRESHOLD_MS,
        },
        'summary': {
            'alert_count': len(alerts),
            'running_builds': running_builds,
            'avg_duration_ms': avg_duration_ms,
            'threshold_ms': TEST_ALERT_THRESHOLD_MS,
        },
        'alerts': alerts,
        'generated_at': now_ms,
    }
