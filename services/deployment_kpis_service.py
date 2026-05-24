from flask import current_app

from collectors.kubernetes_collector import get_cluster_snapshot
from services.docker_image_service import get_latest_image_artifact
from services.parallel_executor import parallel_execute
from services.pipeline_storage_service import get_stored_branch_stage_success_frequency


DEFAULT_DEPLOYMENT_FREQUENCY = {
    'successful': 0,
    'total': 0,
    'rate': 0,
}


def _run_in_app_context(app, func):
    with app.app_context():
        return func()


def get_deployment_rollout_payload():
    try:
        return {
            'connected': True,
            'data': get_cluster_snapshot(),
        }
    except Exception as e:
        return {
            'connected': False,
            'message': str(e),
            'data': {},
        }


def get_deployment_summary_payload():
    try:
        app = current_app._get_current_object()
        tasks = {
            'deployment_frequency': lambda: _run_in_app_context(
                app,
                lambda: get_stored_branch_stage_success_frequency(
                    branch_name='main',
                    stage_name_contains='deploy to aks',
                ),
            ),
            'latest_image': lambda: _run_in_app_context(app, get_latest_image_artifact),
        }
        results = parallel_execute(tasks, max_workers=2, timeout=30)
        return {
            'deployment_frequency': (
                results.get('deployment_frequency')
                or dict(DEFAULT_DEPLOYMENT_FREQUENCY)
            ),
            'latest_image': results.get('latest_image') or {},
        }
    except Exception as e:
        return {
            'deployment_frequency': dict(DEFAULT_DEPLOYMENT_FREQUENCY),
            'latest_image': {},
            'message': str(e),
        }


def merge_deployment_kpis_payload(rollout_payload=None, summary_payload=None):
    rollout_payload = rollout_payload or {'connected': False, 'data': {}}
    summary_payload = summary_payload or {}

    data = dict(rollout_payload.get('data') or {})
    data['deployment_frequency'] = (
        summary_payload.get('deployment_frequency')
        or dict(DEFAULT_DEPLOYMENT_FREQUENCY)
    )
    data['latest_image'] = summary_payload.get('latest_image') or {}

    payload = {
        'connected': bool(rollout_payload.get('connected')),
        'data': data,
    }

    message = rollout_payload.get('message') or summary_payload.get('message')
    if message and not payload['connected']:
        payload['message'] = message

    return payload


def get_deployment_kpis():
    return merge_deployment_kpis_payload(
        get_deployment_rollout_payload(),
        get_deployment_summary_payload(),
    )
