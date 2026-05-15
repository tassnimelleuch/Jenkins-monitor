import logging
from datetime import date, datetime, timedelta, timezone

from flask import current_app
from services.parallel_executor import parallel_execute
from collectors.github_collector import (
    get_branches,
    get_commit,
    get_commits_for_branch,
    get_latest_commit_for_branch,
    get_pull_requests,
    get_repo,
    enrich_commits_with_files,
)
from collectors.jenkins_collector import (
    get_all_builds,
    get_last_failed_build,
    get_build_info,
    extract_build_commit_sha,
    extract_build_commits,
    extract_build_culprits,
)

logger = logging.getLogger(__name__)
MAIN_PIPELINE_BRANCH = 'main'
BRANCH_HEAD_COMMIT_LIMIT = 12
UNAUTHENTICATED_BRANCH_HEAD_COMMIT_LIMIT = 6
AUTHENTICATED_24H_DETAIL_LIMIT = 40
UNAUTHENTICATED_24H_DETAIL_LIMIT = 12


def is_tag_branch_allowed(branch_name):
    normalized_branch_name = (branch_name or '').strip()
    return normalized_branch_name == 'main' or normalized_branch_name.startswith('release/')


def _normalize_person_name(name):
    return ' '.join(str(name or '').split()).casefold()


def _select_failed_pipeline_commit_sha(build_info, build_commits, culprits):
    culprit_names = [
        _normalize_person_name((culprit or {}).get('full_name'))
        for culprit in (culprits or [])
        if _normalize_person_name((culprit or {}).get('full_name'))
    ]
    for culprit_name in culprit_names:
        for commit in build_commits or []:
            if _normalize_person_name((commit or {}).get('author_name')) == culprit_name and commit.get('sha'):
                return commit.get('sha')
    return extract_build_commit_sha(build_info)


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


def _github_token_configured():
    return bool(str(current_app.config.get('GITHUB_TOKEN') or '').strip())


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
        'tagging_allowed': is_tag_branch_allowed(branch_name),
        'date': author.get('date') or committer.get('date'),
        'html_url': c.get('html_url'),
    }


def _branch_head_commit_limit():
    return (
        BRANCH_HEAD_COMMIT_LIMIT
        if _github_token_configured()
        else UNAUTHENTICATED_BRANCH_HEAD_COMMIT_LIMIT
    )


def _detail_commit_limit_last_24h():
    return (
        AUTHENTICATED_24H_DETAIL_LIMIT
        if _github_token_configured()
        else UNAUTHENTICATED_24H_DETAIL_LIMIT
    )


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


def _extract_commit_churn_totals(commit_raw):
    stats = (commit_raw or {}).get('stats', {}) or {}
    additions = int(stats.get('additions', 0) or 0)
    deletions = int(stats.get('deletions', 0) or 0)

    if additions or deletions:
        return additions, deletions

    additions = 0
    deletions = 0
    for file_obj in (commit_raw or {}).get('files', []) or []:
        additions += int(file_obj.get('additions', 0) or 0)
        deletions += int(file_obj.get('deletions', 0) or 0)

    return additions, deletions


def _calculate_file_changes(commits_raw, since_date=None, since_datetime=None):
    return _calculate_file_changes_with_limit(
        commits_raw,
        since_date=since_date,
        since_datetime=since_datetime,
        limit=10,
    )


def _calculate_file_changes_with_limit(commits_raw, since_date=None, since_datetime=None, limit=10):
    if not commits_raw:
        return []

    file_changes = {}
    for commit in commits_raw:
        commit_dt = _parse_commit_datetime(commit)
        
        # Filter based on datetime first (for 24-hour precision), then date
        if since_datetime and commit_dt and commit_dt < since_datetime:
            continue
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
                    'touches': 0,
                    'line_changes': 0,
                    'additions': 0,
                    'deletions': 0,
                    'added': 0,
                    'modified': 0,
                    'removed': 0,
                    'renamed': 0,
                }

            entry = file_changes[filename]
            entry['touches'] += 1
            entry['additions'] += int(file_obj.get('additions', 0) or 0)
            entry['deletions'] += int(file_obj.get('deletions', 0) or 0)
            entry[_normalize_file_status(file_obj)] += 1

    for entry in file_changes.values():
        entry['line_changes'] = entry['additions'] + entry['deletions']
        entry['changes'] = entry['touches']

    sorted_items = sorted(
        file_changes.values(),
        key=lambda item: (-item['line_changes'], -item['touches'], item['filename'])
    )
    if limit is None:
        return sorted_items
    return sorted_items[:limit]


def _count_commits_with_file_details(commits_raw):
    return sum(
        1
        for commit in (commits_raw or [])
        if (commit.get('files') or commit.get('stats'))
    )


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


def _empty_last_24h_file_change_dataset():
    return {
        'items': [],
        'period_count': 1,
        'scope_label': 'Top 5 most changed files in the last 24 hours',
        'ranking_label': 'Ranked by total lines changed',
        'commit_count': 0,
        'detail_commit_count': 0,
        'total_files': 0,
        'total_line_changes': 0,
        'total_additions': 0,
        'total_deletions': 0,
        'total_touches': 0,
    }


def _empty_last_24h_code_churn_dataset():
    return {
        'scope_label': 'Code churn in the last 24 hours',
        'commit_count': 0,
        'detail_commit_count': 0,
        'changed_files': 0,
        'additions': 0,
        'deletions': 0,
        'total_lines_changed': 0,
        'net_change': 0,
    }


def _build_last_24h_scope_label(base_label, detailed_commit_count, total_commit_count):
    if total_commit_count > detailed_commit_count > 0:
        return (
            f'{base_label} '
            f'(based on the latest {detailed_commit_count} of {total_commit_count} main commits with file details)'
        )
    if total_commit_count > 0 and detailed_commit_count == 0:
        return f'{base_label} (GitHub did not return file-level commit details)'
    return base_label


def _build_last_24h_file_change_dataset(commits_raw, total_commit_count=None):
    dataset = _empty_last_24h_file_change_dataset()
    detail_commit_count = _count_commits_with_file_details(commits_raw)
    total_commit_count = int(total_commit_count or detail_commit_count)
    dataset['commit_count'] = total_commit_count
    dataset['detail_commit_count'] = detail_commit_count
    dataset['scope_label'] = _build_last_24h_scope_label(
        dataset['scope_label'],
        detail_commit_count,
        total_commit_count,
    )
    if not commits_raw:
        return dataset

    all_items = _calculate_file_changes_with_limit(commits_raw, limit=None)
    dataset['total_files'] = len(all_items)
    dataset['total_line_changes'] = sum(item['line_changes'] for item in all_items)
    dataset['total_additions'] = sum(item['additions'] for item in all_items)
    dataset['total_deletions'] = sum(item['deletions'] for item in all_items)
    dataset['total_touches'] = sum(item['touches'] for item in all_items)
    dataset['items'] = all_items[:5]
    return dataset


def _build_last_24h_code_churn_dataset(commits_raw, file_change_dataset=None, total_commit_count=None):
    dataset = _empty_last_24h_code_churn_dataset()
    detail_commit_count = _count_commits_with_file_details(commits_raw)
    total_commit_count = int(total_commit_count or detail_commit_count)
    dataset['commit_count'] = total_commit_count
    dataset['detail_commit_count'] = detail_commit_count
    dataset['scope_label'] = _build_last_24h_scope_label(
        dataset['scope_label'],
        detail_commit_count,
        total_commit_count,
    )
    if not commits_raw:
        return dataset

    additions = 0
    deletions = 0
    for commit in commits_raw:
        commit_additions, commit_deletions = _extract_commit_churn_totals(commit)
        additions += commit_additions
        deletions += commit_deletions

    dataset['changed_files'] = int((file_change_dataset or {}).get('total_files', 0) or 0)
    dataset['additions'] = additions
    dataset['deletions'] = deletions
    dataset['total_lines_changed'] = additions + deletions
    dataset['net_change'] = additions - deletions
    return dataset


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

    selected_branches = list(branches_raw[:_branch_head_commit_limit()])
    tasks = {
        branch.get('name'): (
            lambda branch_name=branch.get('name'): _run_in_app_context(
                app,
                lambda: get_latest_commit_for_branch(owner, repo, branch_name),
            )
        )
        for branch in selected_branches
        if branch.get('name')
    }
    latest_by_branch = (
        parallel_execute(tasks, max_workers=4, timeout=20)
        if tasks
        else {}
    )

    commits = []
    for branch in selected_branches:
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


def _load_commit_item(owner, repo, sha, branch_name=None):
    if not sha:
        return None

    commit_raw = get_commit(owner, repo, sha)
    if not isinstance(commit_raw, dict):
        return None

    return _commit_item({
        **commit_raw,
        'branch_name': branch_name,
    })


def _sort_builds_desc(builds):
    return sorted(
        [build for build in (builds or []) if isinstance(build, dict)],
        key=lambda build: int(build.get('number') or 0),
        reverse=True,
    )


def _find_fix_build_for_failure(builds, failed_build):
    failed_number = int((failed_build or {}).get('number') or 0)
    if not failed_number:
        return None

    candidate = None
    for build in _sort_builds_desc(builds):
        build_number = int(build.get('number') or 0)
        if build_number == failed_number:
            return candidate
        if build.get('result') == 'SUCCESS':
            candidate = build
    return None


def _build_commit_item_from_build(owner, repo, branch_name, commit_sha, build_commits):
    commit_item = _load_commit_item(owner, repo, commit_sha, branch_name=branch_name)
    if commit_item:
        return commit_item

    fallback_commit = next(
        (item for item in (build_commits or []) if item.get('sha') == commit_sha),
        None,
    )
    if fallback_commit:
        return _fallback_commit_item(
            owner,
            repo,
            commit_sha,
            message=fallback_commit.get('message'),
            author_name=fallback_commit.get('author_name'),
            branch_name=branch_name,
        )

    return _fallback_commit_item(owner, repo, commit_sha, branch_name=branch_name)


def get_github_summary():
    owner, repo = _get_owner_repo()
    if not owner or not repo:
        return {
            'connected': False,
            'message': 'GitHub is not configured. Set GITHUB_OWNER and GITHUB_REPO.',
        }

    app = current_app._get_current_object()
    now_utc = datetime.now(timezone.utc)
    since_24h = now_utc - timedelta(hours=24)
    since_24h_iso = since_24h.isoformat().replace('+00:00', 'Z')
    token_configured = _github_token_configured()
    tasks = {
        'repo': lambda: _run_in_app_context(app, lambda: get_repo(owner, repo)),
        'branches': lambda: _run_in_app_context(app, lambda: get_branches(owner, repo, per_page=100)),
        'main_commits_24h': lambda: _run_in_app_context(
            app,
            lambda: get_commits_for_branch(
                owner,
                repo,
                MAIN_PIPELINE_BRANCH,
                per_page=100,
                page_limit=2 if token_configured else 1,
                since=since_24h_iso,
            ),
        ),
        'builds': lambda: _run_in_app_context(
            app,
            lambda: get_all_builds(branch_name=MAIN_PIPELINE_BRANCH),
        ),
        'pull_requests': lambda: _run_in_app_context(
            app,
            lambda: get_pull_requests(owner, repo, state='all', per_page=30),
        ),
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
    main_branch_commits_24h_raw = _sort_commits_raw([
        commit
        for commit in (results.get('main_commits_24h') or [])
        if isinstance(commit, dict)
    ])
    main_branch_commits_24h_total = len(main_branch_commits_24h_raw)
    analytics_detail_limit = min(main_branch_commits_24h_total, _detail_commit_limit_last_24h())
    main_branch_commits_24h_raw = _run_in_app_context(
        app,
        lambda: enrich_commits_with_files(
            owner,
            repo,
            main_branch_commits_24h_raw,
            max_commits=analytics_detail_limit,
        ),
    )
    analytics_detail_commit_count = _count_commits_with_file_details(main_branch_commits_24h_raw)

    failing_commit = None

    builds = results.get('builds') or []
    failed_build = get_last_failed_build(builds=builds, branch_name=MAIN_PIPELINE_BRANCH)
    if failed_build:
        build_number = failed_build.get('number')
        build_info = get_build_info(build_number, branch_name=MAIN_PIPELINE_BRANCH) if build_number else None
        build_commits = extract_build_commits(build_info)
        culprits = extract_build_culprits(build_info)
        failed_sha = _select_failed_pipeline_commit_sha(build_info, build_commits, culprits)

        commit_items = []
        for item in build_commits:
            sha = item.get('sha')
            fallback = _fallback_commit_item(
                owner,
                repo,
                sha,
                message=item.get('message'),
                author_name=item.get('author_name'),
                branch_name=MAIN_PIPELINE_BRANCH,
            )
            live_commit = _load_commit_item(owner, repo, sha, branch_name=MAIN_PIPELINE_BRANCH) if sha else None
            commit_items.append(_merge_commit_items(live_commit, fallback) if live_commit else fallback)

        if failed_sha:
            failed_commit_item = next(
                (item for item in commit_items if item.get('sha') == failed_sha),
                None,
            )
            live_failed_commit_item = _load_commit_item(
                owner,
                repo,
                failed_sha,
                branch_name=MAIN_PIPELINE_BRANCH,
            )
            if live_failed_commit_item:
                failed_commit_item = (
                    _merge_commit_items(live_failed_commit_item, failed_commit_item)
                    if failed_commit_item
                    else live_failed_commit_item
                )
            if not failed_commit_item:
                failed_commit_item = _fallback_commit_item(
                    owner,
                    repo,
                    failed_sha,
                    branch_name=MAIN_PIPELINE_BRANCH,
                )
            failing_commit = {
                'pipeline_branch': MAIN_PIPELINE_BRANCH,
                'build_number': build_number,
                'build_result': failed_build.get('result'),
                'build_timestamp': failed_build.get('timestamp'),
                'build_url': (build_info or {}).get('url'),
                'culprits': culprits,
                'commits': commit_items,
                'commit': failed_commit_item,
            }

            fix_build = _find_fix_build_for_failure(builds, failed_build)
            if fix_build:
                fix_build_number = fix_build.get('number')
                fix_build_info = (
                    get_build_info(fix_build_number, branch_name=MAIN_PIPELINE_BRANCH)
                    if fix_build_number else None
                )
                fix_build_commits = extract_build_commits(fix_build_info)
                fix_sha = extract_build_commit_sha(fix_build_info)
                if fix_sha:
                    failing_commit['fix_commit'] = _build_commit_item_from_build(
                        owner,
                        repo,
                        MAIN_PIPELINE_BRANCH,
                        fix_sha,
                        fix_build_commits,
                    )
                    failing_commit['fix_build_number'] = fix_build_number
                    failing_commit['fix_build_timestamp'] = fix_build.get('timestamp')
                    failing_commit['fix_build_url'] = (fix_build_info or {}).get('url')
                    failing_commit['fix_same_sha'] = (fix_sha == failed_sha)

    code_churn_by_period = {'week': [], 'month': []}
    code_churn_24h = _empty_last_24h_code_churn_dataset()
    file_changes_by_period = {
        'week': {'items': [], 'period_count': 0, 'scope_label': 'No recent file activity'},
        'month': {'items': [], 'period_count': 0, 'scope_label': 'No recent file activity'},
        '24h': _empty_last_24h_file_change_dataset(),
    }
    code_churn_list = []
    file_changes = []

    file_changes_by_period['24h'] = _build_last_24h_file_change_dataset(
        main_branch_commits_24h_raw,
        total_commit_count=main_branch_commits_24h_total,
    )
    code_churn_24h = _build_last_24h_code_churn_dataset(
        main_branch_commits_24h_raw,
        file_changes_by_period['24h'],
        total_commit_count=main_branch_commits_24h_total,
    )
    file_changes = file_changes_by_period['24h']['items']
    analytics_notice = None
    if not token_configured:
        analytics_notice = (
            'Detailed GitHub analytics are running without GITHUB_TOKEN, so GitHub may rate-limit '
            'commit file details and user enrichment.'
        )
    elif main_branch_commits_24h_total > analytics_detail_commit_count and analytics_detail_commit_count > 0:
        analytics_notice = (
            f'24-hour analytics are based on {analytics_detail_commit_count} of '
            f'{main_branch_commits_24h_total} main commits with detailed file data.'
        )
    elif main_branch_commits_24h_total > 0 and analytics_detail_commit_count == 0:
        analytics_notice = (
            'GitHub returned recent commits, but file-level details were unavailable for the 24-hour analytics.'
        )

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
        'analytics_mode': 'full_history',
        'analytics_message': 'Analytics based on recent main-branch commit history.',
        'analytics_notice': analytics_notice,
        'commit_scope_label': (
            f'Most recent commit on the first {len(commits)} branches returned by GitHub'
            if branches_raw and len(commits) < len(branches_raw or [])
            else 'Most recent commit on each branch'
        ),
        'commits': commits,
        'failing_commit': failing_commit,
        'code_churn_24h': code_churn_24h,
        'code_churn': code_churn_list,
        'code_churn_by_period': code_churn_by_period,
        'file_changes': file_changes,
        'file_changes_by_period': file_changes_by_period,
        'pull_requests_open': prs_open,
        'pull_requests_merged': prs_merged,
        'pull_requests_closed': prs_closed,
    }
