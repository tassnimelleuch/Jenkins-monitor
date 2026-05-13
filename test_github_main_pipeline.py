from flask import Flask
from unittest.mock import patch

from services.github_service import (
    MAIN_PIPELINE_BRANCH,
    _select_failed_pipeline_commit_sha,
    get_github_summary,
)


def _build_test_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY='test-secret',
        TESTING=True,
        GITHUB_OWNER='octocat',
        GITHUB_REPO='hello-world',
    )
    return app


def _run_tasks(tasks, max_workers=None, timeout=None):
    return {name: task() for name, task in tasks.items()}


def _github_commit_raw(sha, author_name, login, message='Commit message', date='2026-05-13T10:00:00Z'):
    return {
        'sha': sha,
        'commit': {
            'message': message,
            'author': {
                'name': author_name,
                'date': date,
            },
            'committer': {
                'name': author_name,
                'date': date,
            },
        },
        'author': {
            'login': login,
            'avatar_url': f'https://avatars.example/{login}.png',
            'html_url': f'https://github.com/{login}',
        },
        'committer': {
            'login': login,
            'avatar_url': f'https://avatars.example/{login}.png',
            'html_url': f'https://github.com/{login}',
        },
        'html_url': f'https://github.com/octocat/hello-world/commit/{sha}',
    }


def test_select_failed_pipeline_commit_sha_prefers_culprit_commit():
    build_commits = [
        {'sha': '1111111', 'author_name': 'Bob Reviewer'},
        {'sha': '2222222', 'author_name': 'Alice Dev'},
    ]
    culprits = [
        {'full_name': 'Alice Dev'},
    ]

    selected_sha = _select_failed_pipeline_commit_sha(
        {'actions': []},
        build_commits,
        culprits,
    )

    assert selected_sha == '2222222'


def test_github_summary_uses_main_pipeline_failure_for_pr_merge_commit():
    app = _build_test_app()

    with app.app_context(), patch(
        'services.github_service.parallel_execute',
        side_effect=_run_tasks,
    ), patch(
        'services.github_service.get_repo',
        return_value={
            'name': 'hello-world',
            'full_name': 'octocat/hello-world',
            'html_url': 'https://github.com/octocat/hello-world',
        },
    ), patch(
        'services.github_service.get_branches',
        return_value=[],
    ), patch(
        'services.github_service.get_commits_for_branch',
        return_value=[
            _github_commit_raw('9999999', 'Bob Fixer', 'bob', message='Fix main after failure'),
        ],
    ), patch(
        'services.github_service.get_last_failed_build',
        return_value={'number': 42, 'result': 'FAILURE', 'timestamp': 1234567890},
    ) as get_last_failed_build_mock, patch(
        'services.github_service.get_pull_requests',
        return_value=[],
    ), patch(
        'services.github_service.get_build_info',
        return_value={'url': 'https://jenkins.example/job/pipeline/job/main/42/'},
    ) as get_build_info_mock, patch(
        'services.github_service.extract_build_commits',
        return_value=[
            {
                'sha': 'abc1234',
                'message': 'Feature commit from merged PR',
                'author_name': 'Alice Dev',
            }
        ],
    ), patch(
        'services.github_service.extract_build_culprits',
        return_value=[{'full_name': 'Alice Dev', 'url': 'https://jenkins.example/user/alice'}],
    ), patch(
        'services.github_service.get_commit',
        return_value=_github_commit_raw('abc1234', 'Alice Dev', 'alice', message='Feature commit from merged PR'),
    ) as get_commit_mock:
        summary = get_github_summary()

    get_last_failed_build_mock.assert_called_once_with(branch_name=MAIN_PIPELINE_BRANCH)
    get_build_info_mock.assert_called_once_with(42, branch_name=MAIN_PIPELINE_BRANCH)
    get_commit_mock.assert_called_once_with('octocat', 'hello-world', 'abc1234')
    assert summary['failing_commit']['pipeline_branch'] == MAIN_PIPELINE_BRANCH
    assert summary['failing_commit']['commit']['sha'] == 'abc1234'
    assert summary['failing_commit']['commit']['author_login'] == 'alice'
    assert summary['failing_commit']['commit']['author_name'] == 'Alice Dev'
    assert summary['failing_commit']['fix_commit']['sha'] == '9999999'
