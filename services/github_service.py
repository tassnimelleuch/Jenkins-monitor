from flask import current_app
from providers.github import get_repo, get_commits
from providers.jenkins import (
    get_last_failed_build,
    get_build_info,
    extract_build_commit_sha,
    extract_build_commits,
    extract_build_culprits,
)
from providers.github import get_repo, get_commits, get_commit

def _derive_repo_from_project_key(project_key):
    if not project_key or '_' not in project_key:
        return None, None
    owner, repo = project_key.split('_', 1)
    return owner or None, repo or None


def _get_owner_repo():
    owner = current_app.config.get('GITHUB_OWNER')
    repo = current_app.config.get('GITHUB_REPO')

    if owner and repo:
        return owner, repo

    project_key = current_app.config.get('SONARCLOUD_PROJECT_KEY')
    d_owner, d_repo = _derive_repo_from_project_key(project_key)
    return owner or d_owner, repo or d_repo


def _commit_item(c):
    commit = c.get('commit', {}) if isinstance(c, dict) else {}
    author = commit.get('author', {}) or {}
    committer = commit.get('committer', {}) or {}
    author_user = c.get('author') or {}
    committer_user = c.get('committer') or {}
    sha = c.get('sha')
    return {
        'sha': sha,
        'short_sha': sha[:7] if sha else None,
        'message': commit.get('message'),
        'author_name': author.get('name') or committer.get('name'),
        'author_login': author_user.get('login'),
        'committer_name': committer.get('name'),
        'committer_login': committer_user.get('login'),
        'date': author.get('date') or committer.get('date'),
        'html_url': c.get('html_url'),
    }


def get_github_summary():
    owner, repo = _get_owner_repo()
    if not owner or not repo:
        return {
            'connected': False,
            'message': 'GitHub is not configured. Set GITHUB_OWNER and GITHUB_REPO.',
        }

    repo_raw = get_repo(owner, repo)
    commits_raw = get_commits(owner, repo, per_page=8)

    if repo_raw is None and commits_raw is None:
        return {
            'connected': False,
            'message': 'Unable to fetch GitHub data.',
        }

    commits = []
    if isinstance(commits_raw, list):
        commits = [_commit_item(c) for c in commits_raw]

    return {
        'connected': True,
        'owner': owner,
        'repo': repo,
        'repo_info': {
            'name': repo_raw.get('name') if repo_raw else repo,
            'full_name': repo_raw.get('full_name') if repo_raw else f'{owner}/{repo}',
            'description': repo_raw.get('description') if repo_raw else None,
            'default_branch': repo_raw.get('default_branch') if repo_raw else None,
            'language': repo_raw.get('language') if repo_raw else None,
            'stars': repo_raw.get('stargazers_count') if repo_raw else None,
            'forks': repo_raw.get('forks_count') if repo_raw else None,
            'open_issues': repo_raw.get('open_issues_count') if repo_raw else None,
            'updated_at': repo_raw.get('updated_at') if repo_raw else None,
            'html_url': repo_raw.get('html_url') if repo_raw else f'https://github.com/{owner}/{repo}',
        },
        'commits': commits,
    }


def get_github_summary():
    owner, repo = _get_owner_repo()
    if not owner or not repo:
        return {
            'connected': False,
            'message': 'GitHub is not configured. Set GITHUB_OWNER and GITHUB_REPO.',
        }

    repo_raw = get_repo(owner, repo)
    commits_raw = get_commits(owner, repo, per_page=8)

    if repo_raw is None and commits_raw is None:
        return {
            'connected': False,
            'message': 'Unable to fetch GitHub data.',
        }

    commits = []
    if isinstance(commits_raw, list):
        commits = [_commit_item(c) for c in commits_raw]

    failing_commit = None

    failed_build = get_last_failed_build()
    if failed_build:
        build_number = failed_build.get('number')
        build_info = get_build_info(build_number) if build_number else None
        failed_sha = extract_build_commit_sha(build_info)
        build_commits = extract_build_commits(build_info)
        culprits = extract_build_culprits(build_info)

        commit_items = []
        for item in build_commits:
            sha = item.get('sha')
            commit_raw = get_commit(owner, repo, sha) if sha else None
            if commit_raw:
                commit_items.append(_commit_item(commit_raw))
            else:
                commit_items.append({
                    'sha': sha,
                    'short_sha': sha[:7] if sha else None,
                    'message': item.get('message'),
                    'author_name': item.get('author_name'),
                    'author_login': None,
                    'committer_name': None,
                    'committer_login': None,
                    'date': None,
                    'html_url': f'https://github.com/{owner}/{repo}/commit/{sha}' if sha else None,
                })

        if failed_sha:
            commit_raw = get_commit(owner, repo, failed_sha)
            failing_commit = {
                'build_number': build_number,
                'build_result': failed_build.get('result'),
                'build_timestamp': failed_build.get('timestamp'),
                'build_url': (build_info or {}).get('url'),
                'culprits': culprits,
                'commits': commit_items,
                'commit': _commit_item(commit_raw) if commit_raw else {
                    'sha': failed_sha,
                    'short_sha': failed_sha[:7],
                    'message': None,
                    'author_name': None,
                    'author_login': None,
                    'committer_name': None,
                    'committer_login': None,
                    'date': None,
                    'html_url': f'https://github.com/{owner}/{repo}/commit/{failed_sha}',
                }
            }

    return {
        'connected': True,
        'owner': owner,
        'repo': repo,
        'repo_info': {
            'name': repo_raw.get('name') if repo_raw else repo,
            'full_name': repo_raw.get('full_name') if repo_raw else f'{owner}/{repo}',
            'description': repo_raw.get('description') if repo_raw else None,
            'default_branch': repo_raw.get('default_branch') if repo_raw else None,
            'language': repo_raw.get('language') if repo_raw else None,
            'stars': repo_raw.get('stargazers_count') if repo_raw else None,
            'forks': repo_raw.get('forks_count') if repo_raw else None,
            'open_issues': repo_raw.get('open_issues_count') if repo_raw else None,
            'updated_at': repo_raw.get('updated_at') if repo_raw else None,
            'html_url': repo_raw.get('html_url') if repo_raw else f'https://github.com/{owner}/{repo}',
        },
        'commits': commits,
        'failing_commit': failing_commit,
    }
