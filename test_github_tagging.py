from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from github import github_bp
from services.github_service import is_tag_branch_allowed


def _build_test_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY='test-secret',
        TESTING=True,
        GITHUB_OWNER='octocat',
        GITHUB_REPO='hello-world',
        GITHUB_TOKEN='test-token',
    )
    app.register_blueprint(github_bp)
    return app


def _login(client):
    with client.session_transaction() as session:
        session['username'] = 'tester'


def test_is_tag_branch_allowed_only_for_main_and_release_prefix():
    assert is_tag_branch_allowed('main')
    assert is_tag_branch_allowed('release/2026-05')
    assert not is_tag_branch_allowed('release')
    assert not is_tag_branch_allowed('develop')
    assert not is_tag_branch_allowed('feature/demo')
    assert not is_tag_branch_allowed(None)


def test_create_commit_tag_rejects_ineligible_branch():
    app = _build_test_app()

    with patch(
        'services.access_service.get_active_session_user',
        return_value=SimpleNamespace(role='developer'),
    ), patch(
        'github.routes.get_branches',
        return_value=[{'name': 'feature/demo', 'commit': {'sha': 'abc1234'}}],
    ), patch('github.routes.create_tag') as create_tag_mock:
        client = app.test_client()
        _login(client)

        response = client.post(
            '/api/github/tag',
            json={
                'sha': 'abc1234',
                'branch_name': 'feature/demo',
                'tag_name': 'v1.0.0',
            },
        )

    assert response.status_code == 400
    assert response.get_json() == {
        'error': 'Tags can only be created from the main branch or branches starting with release/.'
    }
    create_tag_mock.assert_not_called()


def test_create_commit_tag_allows_release_branch_head():
    app = _build_test_app()

    with patch(
        'services.access_service.get_active_session_user',
        return_value=SimpleNamespace(role='developer'),
    ), patch(
        'github.routes.get_branches',
        return_value=[{'name': 'release/2026-05', 'commit': {'sha': 'def5678'}}],
    ), patch(
        'github.routes.create_tag',
        return_value={
            'success': True,
            'tag_name': 'v1.0.0',
            'ref': 'refs/tags/v1.0.0',
            'message': 'Tag "v1.0.0" created successfully',
        },
    ) as create_tag_mock:
        client = app.test_client()
        _login(client)

        response = client.post(
            '/api/github/tag',
            json={
                'sha': 'def5678',
                'branch_name': 'release/2026-05',
                'tag_name': 'v1.0.0',
            },
        )

    assert response.status_code == 201
    assert response.get_json()['success'] is True
    create_tag_mock.assert_called_once_with(
        'octocat',
        'hello-world',
        'v1.0.0',
        'def5678',
        None,
    )
