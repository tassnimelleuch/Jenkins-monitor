from flask import current_app
from providers.github import get_repo, get_commits


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
    sha = c.get('sha')
    return {
        'sha': sha,
        'short_sha': sha[:7] if sha else None,
        'message': commit.get('message'),
        'author_name': author.get('name') or committer.get('name'),
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
