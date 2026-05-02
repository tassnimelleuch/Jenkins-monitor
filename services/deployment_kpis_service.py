from flask import current_app

from collectors.kubernetes_collector import get_cluster_snapshot
from services.docker_image_service import get_latest_image_artifact
from services.jenkins_service import get_pipeline_kpis
from services.parallel_executor import parallel_execute


def _run_in_app_context(app, func):
    with app.app_context():
        return func()


def get_deployment_kpis():
    try:
        app = current_app._get_current_object()
        tasks = {
            'cluster': lambda: get_cluster_snapshot(),
            'pipeline': lambda: _run_in_app_context(app, get_pipeline_kpis),
            'latest_image': lambda: _run_in_app_context(app, get_latest_image_artifact),
        }

        results = parallel_execute(tasks, max_workers=3, timeout=30)
        data = results.get('cluster') or {}
        pipeline = results.get('pipeline') or {}

        data['deployment_frequency'] = (
            pipeline.get('deployment_frequency', {})
            if pipeline and pipeline.get('connected')
            else {'successful': 0, 'total': 0, 'rate': 0}
        )
        data['latest_image'] = results.get('latest_image') or {}
        return {
            "connected": True,
            "data": data
        }
    except Exception as e:
        return {
            "connected": False,
            "message": str(e)
        }
