import logging
from flask import current_app
from services.parallel_executor import parallel_execute
from providers.github import get_repo, get_commits, get_commit, get_pull_requests
from providers.jenkins import (
    get_last_failed_build,
    get_build_info,
    extract_build_commit_sha,
    extract_build_commits,
    extract_build_culprits,
)

logger = logging.getLogger(__name__)

def _derive_repo_from_project_key(project_key):
    if not project_key or '_' not in project_key:
        return None, None
    owner, repo = project_key.split('_', 1)
    return owner or None, repo or None


def _get_owner_repo():
    owner = current_app.config.get('GITHUB_OWNER')
    repo = current_app.config.get('GITHUB_REPO')

    if not owner or not repo:
        logger.warning('[GitHub] GITHUB_OWNER and GITHUB_REPO not configured')
        return None, None

    return owner, repo


def _commit_item(c):
    commit = c.get('commit', {}) if isinstance(c, dict) else {}
    author = commit.get('author', {}) or {}
    committer = commit.get('committer', {}) or {}
    author_user = c.get('author') or {}
    committer_user = c.get('committer') or {}
    sha = c.get('sha')
    
    # Use author name from commit metadata first, then from GitHub user object
    author_name = author.get('name') or author_user.get('name') or committer.get('name')
    committer_name = committer.get('name') or committer_user.get('name')
    
    return {
        'sha': sha,
        'short_sha': sha[:7] if sha else None,
        'message': commit.get('message'),
        'author_name': author_name,
        'author_login': author_user.get('login'),
        'author_avatar': author_user.get('avatar_url'),
        'author_profile_url': author_user.get('html_url'),
        'committer_name': committer_name,
        'committer_login': committer_user.get('login'),
        'committer_avatar': committer_user.get('avatar_url'),
        'committer_profile_url': committer_user.get('html_url'),
        'date': author.get('date') or committer.get('date'),
        'html_url': c.get('html_url'),
    }


def _run_in_app_context(app, func):
    with app.app_context():
        return func()


def _calculate_code_churn(commits_raw):
    """Calculate code churn (additions/deletions) by month."""
    if not commits_raw:
        return {}
    
    churn_by_month = {}  # {YYYY-MM: {'additions': int, 'deletions': int}}
    
    for commit in commits_raw:
        # Get commit date
        commit_obj = commit.get('commit', {})
        author = commit_obj.get('author', {}) or {}
        date_str = author.get('date')  # ISO format: 2024-03-15T10:30:00Z
        
        if not date_str:
            continue
        
        # Extract YYYY-MM from date
        try:
            month_key = date_str[:7]  # e.g., "2024-03"
        except:
            continue
        
        # Get additions and deletions
        stats = commit.get('stats', {}) or {}
        additions = stats.get('additions', 0) or 0
        deletions = stats.get('deletions', 0) or 0
        
        if month_key not in churn_by_month:
            churn_by_month[month_key] = {'additions': 0, 'deletions': 0}
        
        churn_by_month[month_key]['additions'] += additions
        churn_by_month[month_key]['deletions'] += deletions
    
    # Sort by month and return
    sorted_churn = {}
    for month in sorted(churn_by_month.keys()):
        sorted_churn[month] = churn_by_month[month]
    
    return sorted_churn


def _calculate_file_changes(commits_raw):
    """Calculate most frequently changed files across all commits."""
    if not commits_raw:
        return []
    
    file_changes = {}  # {filename: count}
    
    for commit in commits_raw:
        files = commit.get('files', [])
        if not files:
            continue
        
        for file_obj in files:
            filename = file_obj.get('filename')
            if filename:
                file_changes[filename] = file_changes.get(filename, 0) + 1
    
    # Sort by frequency (descending) and get top 10
    sorted_files = sorted(file_changes.items(), key=lambda x: x[1], reverse=True)[:10]
    
    return [
        {'filename': fname, 'changes': count}
        for fname, count in sorted_files
    ]


def _pr_item(pr):
    """Format a pull request for display."""
    user = pr.get('user', {}) or {}
    return {
        'number': pr.get('number'),
        'title': pr.get('title'),
        'state': pr.get('state'),  # 'open' or 'closed'
        'author_name': user.get('name'),
        'author_login': user.get('login'),
        'author_avatar': user.get('avatar_url'),
        'author_profile_url': user.get('html_url'),
        'url': pr.get('html_url'),
        'created_at': pr.get('created_at'),
        'updated_at': pr.get('updated_at'),
        'closed_at': pr.get('closed_at'),
        'merged_at': pr.get('merged_at'),
        'draft': pr.get('draft', False),
        'additions': pr.get('additions', 0),
        'deletions': pr.get('deletions', 0),
        'changed_files': pr.get('changed_files', 0),
        'comments': pr.get('comments', 0),
        'review_comments': pr.get('review_comments', 0),
    }


def _calculate_file_changes(commits_raw):
    """Calculate most frequently changed files across all commits."""
    if not commits_raw:
        return []
    
    file_changes = {}  # {filename: count}
    
    for commit in commits_raw:
        files = commit.get('files', [])
        if not files:
            continue
        
        for file_obj in files:
            filename = file_obj.get('filename')
            if filename:
                file_changes[filename] = file_changes.get(filename, 0) + 1
    
    # Sort by frequency (descending) and get top 10
    sorted_files = sorted(file_changes.items(), key=lambda x: x[1], reverse=True)[:10]
    
    return [
        {'filename': fname, 'changes': count}
        for fname, count in sorted_files
    ]


def _fetch_commit_details(app, owner, repo, commits_raw):
    if not commits_raw:
        return []

    tasks = {
        item.get('sha'): (
            lambda s=item.get('sha'): _run_in_app_context(app, lambda: get_commit(owner, repo, s))
        )
        for item in commits_raw
        if item.get('sha')
    }
    details_by_sha = (
        parallel_execute(tasks, max_workers=6, timeout=20)
        if tasks
        else {}
    )

    detailed = []
    for item in commits_raw:
        sha = item.get('sha')
        commit_raw = details_by_sha.get(sha) if sha else None
        detailed.append(commit_raw or item)
    return detailed


def get_github_summary():
    owner, repo = _get_owner_repo()
    if not owner or not repo:
        return {
            'connected': False,
            'message': 'GitHub is not configured. Set GITHUB_OWNER and GITHUB_REPO.',
        }

    app = current_app._get_current_object()
    tasks = {
        'repo': lambda: _run_in_app_context(app, lambda: get_repo(owner, repo)),
        'commits': lambda: _run_in_app_context(app, lambda: get_commits(owner, repo, per_page=100, since="2026-04-01T00:00:00Z", until="2026-04-30T23:59:59Z")),
        'failed_build': lambda: _run_in_app_context(app, get_last_failed_build),
        'pull_requests': lambda: _run_in_app_context(app, lambda: get_pull_requests(owner, repo, state='all', per_page=50)),
    }
    results = parallel_execute(tasks, max_workers=4, timeout=20)
    repo_raw = results.get('repo')
    commits_raw = results.get('commits')

    if repo_raw is None and commits_raw is None:
        return {
            'connected': False,
            'message': 'Unable to fetch GitHub data.',
        }

    commits = []
    detailed_commits_raw = []
    if isinstance(commits_raw, list):
        logger.info(f"[GitHub] Fetched {len(commits_raw)} commits. Sample dates: {[c.get('commit', {}).get('author', {}).get('date') for c in commits_raw[:3]]}")
        commits = [_commit_item(c) for c in commits_raw]
        detailed_commits_raw = _fetch_commit_details(app, owner, repo, commits_raw)

    failing_commit = None

    failed_build = results.get('failed_build')
    if failed_build:
        build_number = failed_build.get('number')
        build_info = get_build_info(build_number) if build_number else None
        failed_sha = extract_build_commit_sha(build_info)
        build_commits = extract_build_commits(build_info)
        culprits = extract_build_culprits(build_info)

        commit_items = []
        commit_tasks = {
            item.get('sha'): (
                lambda s=item.get('sha'): _run_in_app_context(app, lambda: get_commit(owner, repo, s))
            )
            for item in build_commits
            if item.get('sha')
        }
        commits_by_sha = (
            parallel_execute(commit_tasks, max_workers=6, timeout=20)
            if commit_tasks
            else {}
        )
        for item in build_commits:
            sha = item.get('sha')
            commit_raw = commits_by_sha.get(sha) if sha else None
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
            commit_raw = _run_in_app_context(app, lambda: get_commit(owner, repo, failed_sha))
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
            
            if commits:
                failed_commit_index = None
                for idx, commit in enumerate(commits):
                    if commit.get('sha') == failed_sha:
                        failed_commit_index = idx
                        break
                
                # The fixing commit is the first one after the failing commit
                if failed_commit_index is not None and failed_commit_index > 0:
                    fix_commit = commits[failed_commit_index - 1]  # More recent commits come first
                    failing_commit['fix_commit'] = fix_commit
                elif not failed_commit_index and commits:
                    # If the failing commit is not in the list but we have recent commits,
                    # try to assume the most recent commit might have fixed it
                    # (This handles cases where the failing commit is very old)
                    potential_fix = commits[0]
                    if potential_fix and potential_fix.get('sha') != failed_sha:
                        failing_commit['fix_commit'] = potential_fix

    # Calculate code churn by month
    code_churn = _calculate_code_churn(detailed_commits_raw) if detailed_commits_raw else {}
    code_churn_list = [
        {'month': month, 'additions': data['additions'], 'deletions': data['deletions']}
        for month, data in code_churn.items()
    ]
    
    # Calculate most frequently changed files
    file_changes = _calculate_file_changes(detailed_commits_raw) if detailed_commits_raw else []

    # Process pull requests
    prs_open = []
    prs_closed = []
    prs_merged = []
    
    prs_all_raw = results.get('pull_requests')
    if isinstance(prs_all_raw, list):
        logger.info(f"[GitHub] Fetched {len(prs_all_raw)} total pull requests")
        all_prs = [_pr_item(pr) for pr in prs_all_raw]
        
        # Separate by state and merge status
        for pr in all_prs:
            if pr.get('merged_at'):
                # Merged PRs
                prs_merged.append(pr)
            elif pr.get('state') == 'open':
                # Open PRs (including drafts)
                prs_open.append(pr)
            else:
                # Closed (unmerged) PRs
                prs_closed.append(pr)

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
        'code_churn': code_churn_list,
        'file_changes': file_changes,
        'pull_requests_open': prs_open,
        'pull_requests_merged': prs_merged,
        'pull_requests_closed': prs_closed,
    }
