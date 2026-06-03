import re

from flask import current_app
from collectors.docker_image_collector import (
    build_image_metadata,
    find_repository_tag_for_build,
    get_latest_image_metadata,
    get_repository_tag,
)
from collectors.jenkins_collector import get_all_builds, get_console_log, get_stages


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


def _docker_push_stage_passed(stages):
    if not stages:
        return False

    for stage in stages:
        name = (stage.get('name') or '').strip().lower()
        status = (stage.get('status') or '').strip().upper()
        if status != 'SUCCESS':
            continue
        if 'push' not in name:
            continue
        if any(marker in name for marker in ('docker', 'image', 'dockerhub', 'registry')):
            return True
    return False


def _build_console_image_metadata(build, image_name, tag):
    if not image_name and not tag:
        return {}

    tag_data = get_repository_tag(tag=tag, image_name=image_name) if tag else None
    if tag_data:
        return build_image_metadata(tag_data, build=build) or {}

    return {
        'source': 'Jenkins Console',
        'build_number': build.get('number'),
        'image_name': image_name or (current_app.config.get('DOCKERHUB_IMAGE') or None),
        'tag': tag,
        'size_mb': None,
        'result': build.get('result'),
        'status': 'Built',
        'timestamp': build.get('timestamp'),
    }


def get_latest_image_artifact(search_limit=12):
    target_branch = (current_app.config.get('JENKINS_BRANCH') or 'main').strip()
    finished_builds = [
        build
        for build in (get_all_builds() or [])
        if build.get('result') is not None
    ]
    for build in finished_builds[:search_limit]:
        build_number = build.get('number')
        if not build_number:
            continue

        stages = get_stages(build_number)
        if not _docker_push_stage_passed(stages):
            continue

        branch_name = (build.get('branch') or build.get('displayName') or target_branch or '').strip()
        tag_data = find_repository_tag_for_build(
            build_number,
            branch_name=branch_name or None,
        )
        if tag_data:
            return build_image_metadata(tag_data, build=build) or {}

        log_text = get_console_log(build_number)
        if not log_text or log_text.startswith('[ERROR]'):
            continue

        image_name, tag = _extract_image_from_log(log_text)
        metadata = _build_console_image_metadata(build, image_name, tag)
        if metadata:
            return metadata

    configured_tag = (current_app.config.get('DOCKERHUB_TAG') or '').strip()
    if configured_tag:
        metadata = get_latest_image_metadata(tag=configured_tag) or {}
        if metadata:
            return metadata

    return get_latest_image_metadata() or {}
