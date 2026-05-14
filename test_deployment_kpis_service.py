from flask import Flask
from unittest.mock import patch

from services.deployment_kpis_service import get_deployment_kpis


def _build_test_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY='test-secret',
        TESTING=True,
    )
    return app


def test_get_deployment_kpis_uses_persisted_main_branch_deployment_frequency():
    app = _build_test_app()

    def run_tasks(tasks, max_workers, timeout):
        return {name: task() for name, task in tasks.items()}

    with app.app_context(), patch(
        'services.deployment_kpis_service.parallel_execute',
        side_effect=run_tasks,
    ), patch(
        'services.deployment_kpis_service.get_cluster_snapshot',
        return_value={'pods_total': 12},
    ), patch(
        'services.deployment_kpis_service.get_latest_image_artifact',
        return_value={},
    ), patch(
        'services.deployment_kpis_service.get_stored_branch_stage_success_frequency',
        return_value={'successful': 47, 'total': 100, 'rate': 47.0},
    ) as deployment_frequency_mock:
        result = get_deployment_kpis()

    deployment_frequency_mock.assert_called_once_with(
        branch_name='main',
        stage_name_contains='deploy to aks',
    )
    assert result == {
        'connected': True,
        'data': {
            'pods_total': 12,
            'deployment_frequency': {
                'successful': 47,
                'total': 100,
                'rate': 47.0,
            },
            'latest_image': {},
        },
    }
