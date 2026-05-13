from flask import Flask
from unittest.mock import patch

from collectors import docker_image_collector as collector


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


def _build_test_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY='test-secret',
        TESTING=True,
        DOCKERHUB_API_URL='https://hub.docker.com/v2',
        DOCKERHUB_IMAGE='tasnimelleuchenis/django-contact-app',
        DOCKERHUB_USERNAME='tasnimelleuchenis',
    )
    return app


def test_get_json_exchanges_pat_for_jwt():
    app = _build_test_app()
    app.config['DOCKERHUB_TOKEN'] = 'dckr_pat_example'
    collector._DOCKERHUB_JWT_CACHE.clear()

    with app.app_context(), patch(
        'collectors.docker_image_collector.requests.post',
        return_value=_FakeResponse(200, {'access_token': 'header.payload.signature'}),
    ) as post_mock, patch(
        'collectors.docker_image_collector.requests.get',
        return_value=_FakeResponse(200, {'ok': True}),
    ) as get_mock:
        data = collector._get_json('https://hub.docker.com/v2/repositories/demo/repo/tags')

    assert data == {'ok': True}
    post_mock.assert_called_once()
    _, kwargs = get_mock.call_args
    assert kwargs['headers']['Authorization'] == 'Bearer header.payload.signature'


def test_get_json_retries_without_auth_when_bearer_is_rejected():
    app = _build_test_app()
    app.config['DOCKERHUB_TOKEN'] = 'dckr_pat_example'
    collector._DOCKERHUB_JWT_CACHE.clear()

    with app.app_context(), patch(
        'collectors.docker_image_collector.requests.post',
        return_value=_FakeResponse(200, {'access_token': 'header.payload.signature'}),
    ), patch(
        'collectors.docker_image_collector.requests.get',
        side_effect=[
            _FakeResponse(401, {'detail': 'Unauthorized'}),
            _FakeResponse(200, {'results': [{'name': 'latest'}]}),
        ],
    ) as get_mock:
        data = collector._get_json('https://hub.docker.com/v2/repositories/demo/repo/tags')

    assert data == {'results': [{'name': 'latest'}]}
    assert get_mock.call_count == 2
    first_headers = get_mock.call_args_list[0].kwargs['headers']
    second_headers = get_mock.call_args_list[1].kwargs['headers']
    assert 'Authorization' in first_headers
    assert 'Authorization' not in second_headers
