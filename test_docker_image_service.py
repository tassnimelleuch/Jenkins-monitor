from flask import Flask
from unittest.mock import patch

from collectors.docker_image_collector import find_repository_tag_for_build
from services.docker_image_service import get_latest_image_artifact


def _build_test_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY='test-secret',
        TESTING=True,
        DOCKERHUB_IMAGE='tasnimelleuchenis/django-contact-app',
        JENKINS_BRANCH='main',
    )
    return app


def test_find_repository_tag_for_build_prefers_matching_multibranch_prefix():
    app = _build_test_app()

    with app.app_context():
        tag = find_repository_tag_for_build(
            12,
            branch_name='feature/contact-form',
            tag_results=[
                {'name': 'main-2026-05-13-at-09-00-00-build-12'},
                {'name': 'feature-contact-form-2026-05-13-at-10-00-00-build-12'},
            ],
        )

    assert tag['name'] == 'feature-contact-form-2026-05-13-at-10-00-00-build-12'


def test_find_repository_tag_for_build_falls_back_to_first_suffix_match():
    app = _build_test_app()

    with app.app_context():
        tag = find_repository_tag_for_build(
            7,
            branch_name='feature/contact-form',
            tag_results=[
                {'name': '2026-05-13-at-10-00-00-build-7'},
            ],
        )

    assert tag['name'] == '2026-05-13-at-10-00-00-build-7'


def test_get_latest_image_artifact_uses_selected_branch_for_multibranch_tags():
    app = _build_test_app()
    app.config['JENKINS_BRANCH'] = 'feature/contact-form'

    with app.app_context(), patch(
        'services.docker_image_service.get_last_n_finished',
        return_value=[
            {'number': 12, 'result': 'SUCCESS', 'timestamp': 1_715_000_000_000},
        ],
    ), patch(
        'services.docker_image_service.find_repository_tag_for_build',
        return_value={
            'name': 'feature-contact-form-2026-05-13-at-10-00-00-build-12',
            'images': [{'size': 157_286_400}],
        },
    ) as find_tag_mock:
        artifact = get_latest_image_artifact()

    find_tag_mock.assert_called_once_with(12, branch_name='feature/contact-form')
    assert artifact['build_number'] == 12
    assert artifact['image_name'] == 'tasnimelleuchenis/django-contact-app'
    assert artifact['tag'] == 'feature-contact-form-2026-05-13-at-10-00-00-build-12'
    assert artifact['size_mb'] == 150.0
    assert artifact['result'] == 'SUCCESS'
    assert artifact['timestamp'] == 1_715_000_000_000


def test_get_latest_image_artifact_falls_back_to_console_after_docker_stage_success():
    app = _build_test_app()

    with app.app_context(), patch(
        'services.docker_image_service.get_last_n_finished',
        return_value=[
            {'number': 14, 'result': 'SUCCESS', 'timestamp': 1_715_000_100_000},
        ],
    ), patch(
        'services.docker_image_service.find_repository_tag_for_build',
        return_value=None,
    ), patch(
        'services.docker_image_service.get_stages',
        return_value=[
            {'name': 'Docker Image Build', 'status': 'SUCCESS'},
            {'name': 'Push Docker Image', 'status': 'SUCCESS'},
        ],
    ), patch(
        'services.docker_image_service.get_console_log',
        return_value=(
            'Building Docker image: tasnimelleuchenis/django-contact-app:'
            'main-2026-05-13-at-21-40-00-build-14\n'
        ),
    ), patch(
        'services.docker_image_service.get_repository_tag',
        return_value=None,
    ):
        artifact = get_latest_image_artifact()

    assert artifact['source'] == 'Jenkins Console'
    assert artifact['build_number'] == 14
    assert artifact['image_name'] == 'tasnimelleuchenis/django-contact-app'
    assert artifact['tag'] == 'main-2026-05-13-at-21-40-00-build-14'
    assert artifact['size_mb'] is None
    assert artifact['result'] == 'SUCCESS'
    assert artifact['timestamp'] == 1_715_000_100_000


def test_get_latest_image_artifact_ignores_console_when_docker_stage_failed():
    app = _build_test_app()

    with app.app_context(), patch(
        'services.docker_image_service.get_last_n_finished',
        return_value=[
            {'number': 15, 'result': 'FAILURE', 'timestamp': 1_715_000_200_000},
        ],
    ), patch(
        'services.docker_image_service.find_repository_tag_for_build',
        return_value=None,
    ), patch(
        'services.docker_image_service.get_stages',
        return_value=[
            {'name': 'Docker Image Build', 'status': 'FAILED'},
        ],
    ), patch(
        'services.docker_image_service.get_latest_image_metadata',
        return_value={},
    ), patch(
        'services.docker_image_service.get_console_log',
        return_value='Building Docker image: tasnimelleuchenis/django-contact-app:bad-tag\n',
    ):
        artifact = get_latest_image_artifact()

    assert artifact == {}
