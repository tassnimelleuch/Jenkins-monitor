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
        'services.github_service.enrich_commits_with_files',
        side_effect=lambda owner, repo, commits, max_commits=20: commits,
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


def test_github_summary_falls_back_to_enriched_24h_commits_for_top_files():
    app = _build_test_app()

    recent_commits = [
        _github_commit_raw('9999999', 'Bob Fixer', 'bob', message='Newest change', date='2026-05-13T11:30:00Z'),
        _github_commit_raw('8888888', 'Alice Dev', 'alice', message='Older change', date='2026-05-13T08:15:00Z'),
    ]

    recent_commits[1]['parents'] = [{'sha': '7777777'}]

    enriched_by_sha = {
        '9999999': {
            **recent_commits[0],
            'files': [
                {'filename': 'static/js/github.js', 'additions': 18, 'deletions': 4, 'status': 'modified'},
                {'filename': 'templates/github.html', 'additions': 10, 'deletions': 2, 'status': 'modified'},
            ],
        },
        '8888888': {
            **recent_commits[1],
            'files': [
                {'filename': 'static/js/github.js', 'additions': 9, 'deletions': 6, 'status': 'modified'},
                {'filename': 'static/css/github.css', 'additions': 14, 'deletions': 3, 'status': 'modified'},
                {'filename': 'services/github_service.py', 'additions': 8, 'deletions': 7, 'status': 'modified'},
            ],
        },
    }

    def _enrich(owner, repo, commits, max_commits=20):
        return [enriched_by_sha.get(commit.get('sha'), commit) for commit in commits]

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
        side_effect=[recent_commits, recent_commits],
    ), patch(
        'services.github_service.enrich_commits_with_files',
        side_effect=_enrich,
    ), patch(
        'services.github_service.get_last_failed_build',
        return_value=None,
    ), patch(
        'services.github_service.get_pull_requests',
        return_value=[],
    ):
        summary = get_github_summary()

    dataset = summary['file_changes_by_period']['24h']
    churn = summary['code_churn_24h']
    assert dataset['scope_label'] == 'Top 5 most changed files in the last 24 hours'
    assert dataset['commit_count'] == 2
    assert dataset['total_files'] == 4
    assert [item['filename'] for item in dataset['items']] == [
        'static/js/github.js',
        'static/css/github.css',
        'services/github_service.py',
        'templates/github.html',
    ]
    assert dataset['items'][0]['line_changes'] == 37
    assert dataset['items'][0]['touches'] == 2
    assert churn['commit_count'] == 2
    assert churn['changed_files'] == 4
    assert churn['additions'] == 59
    assert churn['deletions'] == 22
    assert churn['total_lines_changed'] == 81
    assert churn['net_change'] == 37
