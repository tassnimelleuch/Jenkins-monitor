import re
import requests
from flask import current_app

from collectors.kubernetes_collectors import get_cluster_snapshot
from collectors.jenkins_collectors import get_all_builds, get_console_log
from services.pipeline_kpis_service import get_pipeline_kpis
from services.parallel_executor import parallel_execute

IMAGE_PATTERNS = [
    r'Building Docker image:\s*([^\s:]+):([^\s]+)',
    r'Docker image built:\s*([^\s:]+):([^\s]+)',
    r'Docker image:\s*([^\s:]+):([^\s]+)',
    r'Updated deployment with new image:\s*([^\s:]+):([^\s]+)',
    r'Applying deployment with image:\s*([^\s:]+):([^\s]+)',
    r'Successfully pushed\s*([^\s:]+):([^\s]+)',
    r'naming to docker\.io/([^\s:]+):([^\s]+)',
]
ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _extract_image_from_log(log_text):
    if not log_text:
        return None, None
    text = ANSI_RE.sub('', log_text)
    for pattern in IMAGE_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
    return None, None


def _normalize_image_name(image_name):
    if not image_name:
        return None
    name = image_name.strip()
    if name.startswith('docker.io/'):
        name = name[len('docker.io/'):]
    return name


def _get_docker_hub_size_mb(image_name, tag):
    name = _normalize_image_name(image_name)
    if not name or not tag:
        return None
    if '/' not in name:
        return None
    namespace, repo = name.split('/', 1)
    url = f'https://hub.docker.com/v2/repositories/{namespace}/{repo}/tags/{tag}/'
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        images = data.get('images') or []
        if not images:
            return None
        size_bytes = images[0].get('size')
        if not size_bytes:
            return None
        return round(size_bytes / (1024 * 1024), 1)
    except Exception:
        return None


def _get_latest_image_artifact():
    builds = get_all_builds()
    if not builds:
        return {}
    for b in builds[:8]:
        build_number = b.get('number')
        if not build_number:
            continue
        log_text = get_console_log(build_number)
        if not log_text or log_text.startswith('[ERROR]'):
            continue
        image_name, tag = _extract_image_from_log(log_text)
        if image_name or tag:
            return {
                "build_number": build_number,
                "image_name": image_name,
                "tag": tag,
                "size_mb": _get_docker_hub_size_mb(image_name, tag),
                "result": b.get('result'),
                "timestamp": b.get('timestamp'),
            }
    return {}


def _run_in_app_context(app, func):
    with app.app_context():
        return func()


def get_deployment_kpis():
    try:
        app = current_app._get_current_object()
        tasks = {
            'cluster': lambda: get_cluster_snapshot(),
            'pipeline': lambda: _run_in_app_context(app, get_pipeline_kpis),
            'latest_image': lambda: _run_in_app_context(app, _get_latest_image_artifact),
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
