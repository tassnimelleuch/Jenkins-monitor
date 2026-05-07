import logging
from datetime import date, datetime, timedelta, timezone

from flask import current_app
from services.parallel_executor import parallel_execute
from collectors.github_collector import (
    get_branches,
    get_latest_commit_for_branch,
    get_pull_requests,
    get_repo,
)
from collectors.jenkins_collector import (
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
    branch_name = c.get('branch_name')
    
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
        'branch_name': branch_name,
        'date': author.get('date') or committer.get('date'),
        'html_url': c.get('html_url'),
    }


def _run_in_app_context(app, func):
    with app.app_context():
        return func()

def _parse_commit_datetime(commit_raw):
    commit_obj = commit_raw.get('commit', {}) if isinstance(commit_raw, dict) else {}
    author = commit_obj.get('author', {}) or {}
    committer = commit_obj.get('committer', {}) or {}
    raw_value = author.get('date') or committer.get('date')
    if not raw_value:
        return None

    try:
        parsed = datetime.fromisoformat(str(raw_value).replace('Z', '+00:00'))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_file_status(file_obj):
    raw_status = str((file_obj or {}).get('status') or 'modified').lower()
    if raw_status == 'added':
        return 'added'
    if raw_status in ('removed', 'deleted'):
        return 'removed'
    if raw_status == 'renamed':
        return 'renamed'
    return 'modified'


def _period_metadata(commit_dt, grouping):
    if grouping == 'month':
        start_date = date(commit_dt.year, commit_dt.month, 1)
        return {
            'period_key': start_date.strftime('%Y-%m'),
            'label': start_date.strftime('%b %y'),
            'detail_label': start_date.strftime('%B %Y'),
            'start_date': start_date.isoformat(),
        }

    week_start = commit_dt.date() - timedelta(days=commit_dt.weekday())
    week_end = week_start + timedelta(days=6)
    iso_year, iso_week, _ = commit_dt.isocalendar()
    return {
        'period_key': f'{iso_year}-W{iso_week:02d}',
        'label': week_start.strftime('%b %d'),
        'detail_label': f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}",
        'start_date': week_start.isoformat(),
    }


def _calculate_code_churn(commits_raw, grouping='month', max_periods=6):
    if not commits_raw:
        return []

    grouped = {}
    for commit in commits_raw:
        commit_dt = _parse_commit_datetime(commit)
        if commit_dt is None:
            continue

        meta = _period_metadata(commit_dt, grouping)
        key = meta['period_key']
        if key not in grouped:
            grouped[key] = {
                **meta,
                'commits': 0,
                'additions': 0,
                'deletions': 0,
                'changed_files': 0,
                'files_added': 0,
                'files_modified': 0,
                'files_removed': 0,
                'files_renamed': 0,
            }

        entry = grouped[key]
        stats = commit.get('stats', {}) or {}
        entry['commits'] += 1
        entry['additions'] += int(stats.get('additions', 0) or 0)
        entry['deletions'] += int(stats.get('deletions', 0) or 0)

        for file_obj in commit.get('files', []) or []:
            entry['changed_files'] += 1
            status = _normalize_file_status(file_obj)
            if status == 'added':
                entry['files_added'] += 1
            elif status == 'removed':
                entry['files_removed'] += 1
            elif status == 'renamed':
                entry['files_renamed'] += 1
            else:
                entry['files_modified'] += 1

    periods = sorted(grouped.values(), key=lambda item: item['start_date'])
    if max_periods and len(periods) > max_periods:
        periods = periods[-max_periods:]
    return periods


def _calculate_file_changes(commits_raw, since_date=None):
    if not commits_raw:
        return []

    file_changes = {}
    for commit in commits_raw:
        commit_dt = _parse_commit_datetime(commit)
        if since_date and commit_dt and commit_dt.date() < since_date:
            continue

        for file_obj in commit.get('files', []) or []:
            filename = file_obj.get('filename')
            if not filename:
                continue

            if filename not in file_changes:
                file_changes[filename] = {
                    'filename': filename,
                    'changes': 0,
                    'additions': 0,
                    'deletions': 0,
                    'added': 0,
                    'modified': 0,
                    'removed': 0,
                    'renamed': 0,
                }

            entry = file_changes[filename]
            entry['changes'] += 1
            entry['additions'] += int(file_obj.get('additions', 0) or 0)
            entry['deletions'] += int(file_obj.get('deletions', 0) or 0)
            entry[_normalize_file_status(file_obj)] += 1

    return sorted(
        file_changes.values(),
        key=lambda item: (-item['changes'], -(item['additions'] + item['deletions']), item['filename'])
    )[:10]


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


def _build_file_change_groups(commits_raw, code_churn_by_period):
    datasets = {}
    for grouping, periods in code_churn_by_period.items():
        if periods:
            since_date = date.fromisoformat(periods[0]['start_date'])
            period_count = len(periods)
        else:
            since_date = None
            period_count = 0

        items = _calculate_file_changes(commits_raw, since_date=since_date)
        label_unit = 'weeks' if grouping == 'week' else 'months'
        datasets[grouping] = {
            'items': items,
            'period_count': period_count,
            'scope_label': (
                f'Top 10 files touched across the last {period_count} {label_unit}'
                if period_count
                else 'No recent file activity'
            ),
        }
    return datasets


def _sort_commits_raw(commits_raw):
    earliest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        [item for item in commits_raw if isinstance(item, dict)],
        key=lambda item: _parse_commit_datetime(item) or earliest,
        reverse=True,
    )


def _fetch_branch_head_commits(app, owner, repo, branches_raw):
    if not branches_raw:
        return []

    tasks = {
        branch.get('name'): (
            lambda branch_name=branch.get('name'): _run_in_app_context(
                app,
                lambda: get_latest_commit_for_branch(owner, repo, branch_name),
            )
        )
        for branch in branches_raw
        if branch.get('name')
    }
    latest_by_branch = (
        parallel_execute(tasks, max_workers=4, timeout=20)
        if tasks
        else {}
    )

    commits = []
    for branch in branches_raw:
        branch_name = branch.get('name')
        commit_raw = latest_by_branch.get(branch_name) if branch_name else None
        if not isinstance(commit_raw, dict):
            continue
        commits.append({**commit_raw, 'branch_name': branch_name})

    return _sort_commits_raw(commits)


def _fallback_commit_item(owner, repo, sha, message=None, author_name=None, date=None, branch_name=None):
    return {
        'sha': sha,
        'short_sha': sha[:7] if sha else None,
        'message': message,
        'author_name': author_name,
        'author_login': None,
        'author_avatar': None,
        'author_profile_url': None,
        'committer_name': None,
        'committer_login': None,
        'committer_avatar': None,
        'committer_profile_url': None,
        'branch_name': branch_name,
        'date': date,
        'html_url': f'https://github.com/{owner}/{repo}/commit/{sha}' if sha else None,
    }


def _merge_commit_items(primary, fallback):
    merged = dict(fallback)
    for key, value in (primary or {}).items():
        if value not in (None, '', []):
            merged[key] = value
    return merged


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
        'branches': lambda: _run_in_app_context(app, lambda: get_branches(owner, repo, per_page=100)),
        'failed_build': lambda: _run_in_app_context(app, get_last_failed_build),
        'pull_requests': lambda: _run_in_app_context(app, lambda: get_pull_requests(owner, repo, state='all', per_page=50)),
    }
    results = parallel_execute(tasks, max_workers=4, timeout=20)
    repo_raw = results.get('repo')
    branches_raw = results.get('branches')

    if repo_raw is None and branches_raw is None:
        return {
            'connected': False,
            'message': 'Unable to fetch GitHub data.',
        }

    branch_head_commits_raw = _fetch_branch_head_commits(app, owner, repo, branches_raw or [])
    commits = [_commit_item(c) for c in branch_head_commits_raw]
    commits_by_sha = {item.get('sha'): item for item in commits if item.get('sha')}

    failing_commit = None

    failed_build = results.get('failed_build')
    if failed_build:
        build_number = failed_build.get('number')
        build_info = get_build_info(build_number) if build_number else None
        failed_sha = extract_build_commit_sha(build_info)
        build_commits = extract_build_commits(build_info)
        culprits = extract_build_culprits(build_info)

        commit_items = []
        for item in build_commits:
            sha = item.get('sha')
            fallback = _fallback_commit_item(
                owner,
                repo,
                sha,
                message=item.get('message'),
                author_name=item.get('author_name'),
            )
            branch_commit = commits_by_sha.get(sha) if sha else None
            commit_items.append(_merge_commit_items(branch_commit, fallback) if branch_commit else fallback)

        if failed_sha:
            failed_commit_item = commits_by_sha.get(failed_sha)
            if not failed_commit_item:
                failed_commit_item = next(
                    (item for item in commit_items if item.get('sha') == failed_sha),
                    None,
                )
            if not failed_commit_item:
                failed_commit_item = _fallback_commit_item(owner, repo, failed_sha)
            failing_commit = {
                'build_number': build_number,
                'build_result': failed_build.get('result'),
                'build_timestamp': failed_build.get('timestamp'),
                'build_url': (build_info or {}).get('url'),
                'culprits': culprits,
                'commits': commit_items,
                'commit': failed_commit_item,
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

    code_churn_by_period = {'week': [], 'month': []}
    file_changes_by_period = {'week': {'items': [], 'period_count': 0, 'scope_label': 'No recent file activity'}, 'month': {'items': [], 'period_count': 0, 'scope_label': 'No recent file activity'}}
    code_churn_list = []
    file_changes = []

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
        'analytics_window': {
            'branches': len(branches_raw or []),
            'branch_heads': len(commits),
        },
        'analytics_mode': 'branch_heads',
        'analytics_message': 'Analytics are limited to the latest commit on each branch to reduce GitHub API requests.',
        'commit_scope_label': 'Latest commit on each branch',
        'commits': commits,
        'failing_commit': failing_commit,
        'code_churn': code_churn_list,
        'code_churn_by_period': code_churn_by_period,
        'file_changes': file_changes,
        'file_changes_by_period': file_changes_by_period,
        'pull_requests_open': prs_open,
        'pull_requests_merged': prs_merged,
        'pull_requests_closed': prs_closed,
    }
