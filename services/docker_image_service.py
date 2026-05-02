from flask import current_app

from collectors.docker_image_collector import (
    build_image_metadata,
    find_repository_tag_for_build,
    get_latest_image_metadata,
)
from collectors.jenkins_collector import get_last_n_finished


def get_latest_image_artifact(search_limit=12):
    configured_tag = (current_app.config.get('DOCKERHUB_TAG') or '').strip()
    if configured_tag:
        return get_latest_image_metadata(tag=configured_tag) or {}

    finished_builds = get_last_n_finished(None) or []
    for build in finished_builds[:search_limit]:
        build_number = build.get('number')
        if not build_number:
            continue

        tag_data = find_repository_tag_for_build(build_number)
        if tag_data:
            return build_image_metadata(tag_data, build=build) or {}

    return get_latest_image_metadata() or {}
