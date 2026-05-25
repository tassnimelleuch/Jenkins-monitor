from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
from typing import Optional

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from collectors.github_collector import get_commit, get_commits_for_branch
from extensions import cache, db
from github_storage_models import GitHubCommit, GitHubCommitFile, GitHubRepoSyncState
from services.parallel_executor import parallel_execute


GITHUB_24H_CACHE_VERSION = 'v2'
GITHUB_24H_CACHE_TIMEOUT_SECONDS = 300
DEFAULT_GITHUB_SYNC_INTERVAL_SECONDS = 60
MIN_GITHUB_SYNC_INTERVAL_SECONDS = 30
MIN_UNAUTHENTICATED_GITHUB_SYNC_INTERVAL_SECONDS = 180
DEFAULT_GITHUB_INITIAL_BACKFILL_HOURS = 48
DEFAULT_GITHUB_RETENTION_DAYS = 30
SYNC_DATASET = 'commit_history'

_github_refresh_lock = threading.Lock()
_github_refresh_in_progress = set()


def _utcnow():
    return datetime.now(timezone.utc)


def _github_token_configured() -> bool:
    return bool(str(current_app.config.get('GITHUB_TOKEN') or '').strip())


def _run_in_app_context(app, func):
    with app.app_context():
        return func()


def _normalize_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime_to_github_iso(value: datetime) -> str:
    return _normalize_datetime(value).isoformat().replace('+00:00', 'Z')


def _parse_commit_datetime(commit_raw) -> Optional[datetime]:
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

    return _normalize_datetime(parsed)


def _github_sync_interval_seconds() -> int:
    configured_interval = int(
        current_app.config.get(
            'GITHUB_SYNC_INTERVAL_SECONDS',
            DEFAULT_GITHUB_SYNC_INTERVAL_SECONDS,
        )
    )
    configured_interval = max(configured_interval, MIN_GITHUB_SYNC_INTERVAL_SECONDS)
    if not _github_token_configured():
        return max(
            configured_interval,
            MIN_UNAUTHENTICATED_GITHUB_SYNC_INTERVAL_SECONDS,
        )
    return configured_interval


def _github_cache_timeout_seconds() -> int:
    return int(
        current_app.config.get(
            'GITHUB_ANALYTICS_CACHE_TIMEOUT_SECONDS',
            GITHUB_24H_CACHE_TIMEOUT_SECONDS,
        )
    )


def _github_initial_backfill_hours() -> int:
    return int(
        current_app.config.get(
            'GITHUB_INITIAL_BACKFILL_HOURS',
            DEFAULT_GITHUB_INITIAL_BACKFILL_HOURS,
        )
    )


def _github_retention_days() -> int:
    return int(
        current_app.config.get(
            'GITHUB_RETENTION_DAYS',
            DEFAULT_GITHUB_RETENTION_DAYS,
        )
    )


def _github_detail_retry_seconds() -> int:
    return int(
        current_app.config.get(
            'GITHUB_DETAIL_RETRY_SECONDS',
            60,
        )
    )


def _github_commit_page_limit() -> int:
    token = str(current_app.config.get('GITHUB_TOKEN') or '').strip()
    return 5 if token else 2


def _analytics_cache_key(owner: str, repo: str, branch_name: str) -> str:
    return f'github_24h:{GITHUB_24H_CACHE_VERSION}:{owner}:{repo}:{branch_name}'


def _sync_state_key(owner: str, repo: str, branch_name: str) -> str:
    return f'github_sync:{owner}:{repo}:{branch_name}:{SYNC_DATASET}'


def _sync_state(owner: str, repo: str, branch_name: str) -> Optional[GitHubRepoSyncState]:
    return GitHubRepoSyncState.query.filter_by(
        owner=owner,
        repo=repo,
        branch_name=branch_name,
        dataset=SYNC_DATASET,
    ).one_or_none()


def _ensure_sync_state(owner: str, repo: str, branch_name: str) -> GitHubRepoSyncState:
    row = _sync_state(owner, repo, branch_name)
    if row is None:
        row = GitHubRepoSyncState(
            owner=owner,
            repo=repo,
            branch_name=branch_name,
            dataset=SYNC_DATASET,
        )
        db.session.add(row)
    return row


def _sync_is_due(state: Optional[GitHubRepoSyncState], max_age_seconds: Optional[int] = None) -> bool:
    if state is None:
        return True

    if max_age_seconds is None:
        max_age_seconds = _github_sync_interval_seconds()

    reference_time = state.last_synced_at or state.last_attempted_at
    if reference_time is None:
        return True

    reference_time = _normalize_datetime(reference_time)
    return (_utcnow() - reference_time) >= timedelta(seconds=max_age_seconds)


def _detail_retry_due(state: Optional[GitHubRepoSyncState]) -> bool:
    if state is None:
        return True

    reference_time = state.last_attempted_at or state.last_synced_at
    if reference_time is None:
        return True

    reference_time = _normalize_datetime(reference_time)
    return (_utcnow() - reference_time) >= timedelta(seconds=max(_github_detail_retry_seconds(), 1))


def _sync_since_datetime(state: Optional[GitHubRepoSyncState], now: datetime) -> datetime:
    retention_floor = now - timedelta(days=max(_github_retention_days(), 2))
    if state is None or state.last_synced_at is None:
        return max(
            retention_floor,
            now - timedelta(hours=max(_github_initial_backfill_hours(), 24)),
        )

    overlap_start = _normalize_datetime(state.last_synced_at) - timedelta(hours=6)
    return max(retention_floor, overlap_start)


def _record_sync_failure(owner: str, repo: str, branch_name: str, exc: Exception):
    try:
        now = _utcnow()
        row = _ensure_sync_state(owner, repo, branch_name)
        row.last_attempted_at = now
        row.last_error = f'{type(exc).__name__}: {exc}'
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to persist GitHub sync failure state for %s/%s [%s].',
            owner,
            repo,
            branch_name,
        )


def _normalize_file_status(raw_status) -> str:
    status = str(raw_status or 'modified').strip().lower()
    if status == 'added':
        return 'added'
    if status in ('removed', 'deleted'):
        return 'removed'
    if status == 'renamed':
        return 'renamed'
    return 'modified'


def _sync_commit_files(commit_row: GitHubCommit, files, now: datetime):
    existing = {row.filename: row for row in (commit_row.files or [])}
    incoming_names = set()

    for file_obj in files or []:
        filename = str(file_obj.get('filename') or '').strip()
        if not filename:
            continue

        incoming_names.add(filename)
        row = existing.get(filename)
        if row is None:
            row = GitHubCommitFile(
                commit_id=commit_row.id,
                filename=filename,
            )
            db.session.add(row)

        row.previous_filename = file_obj.get('previous_filename')
        row.status = _normalize_file_status(file_obj.get('status'))
        row.additions = int(file_obj.get('additions', 0) or 0)
        row.deletions = int(file_obj.get('deletions', 0) or 0)
        row.changes = int(
            file_obj.get('changes', 0)
            or (row.additions + row.deletions)
        )
        row.last_synced_at = now

    for filename, row in existing.items():
        if filename not in incoming_names:
            db.session.delete(row)


def _load_existing_commit_rows(owner: str, repo: str, shas):
    if not shas:
        return {}

    rows = (
        GitHubCommit.query
        .options(selectinload(GitHubCommit.files))
        .filter(
            GitHubCommit.owner == owner,
            GitHubCommit.repo == repo,
            GitHubCommit.sha.in_(shas),
        )
        .all()
    )
    return {row.sha: row for row in rows}


def _upsert_commit_row(
    owner: str,
    repo: str,
    branch_name: str,
    commit_raw,
    detail_raw,
    existing_rows,
    now: datetime,
):
    sha = commit_raw.get('sha')
    if not sha:
        return None

    row = existing_rows.get(sha)
    if row is None:
        row = GitHubCommit(
            owner=owner,
            repo=repo,
            sha=sha,
        )
        db.session.add(row)
        existing_rows[sha] = row

    effective_raw = detail_raw if isinstance(detail_raw, dict) else commit_raw
    commit_obj = effective_raw.get('commit', {}) or {}
    author = commit_obj.get('author', {}) or {}
    committer = commit_obj.get('committer', {}) or {}
    author_user = effective_raw.get('author') or {}
    committer_user = effective_raw.get('committer') or {}

    row.branch_name = branch_name
    row.message = commit_obj.get('message')
    row.author_name = author.get('name') or author_user.get('name') or row.author_name
    row.author_login = author_user.get('login') or row.author_login
    row.author_avatar = author_user.get('avatar_url') or row.author_avatar
    row.author_profile_url = author_user.get('html_url') or row.author_profile_url
    row.committer_name = committer.get('name') or committer_user.get('name') or row.committer_name
    row.committer_login = committer_user.get('login') or row.committer_login
    row.committer_avatar = committer_user.get('avatar_url') or row.committer_avatar
    row.committer_profile_url = committer_user.get('html_url') or row.committer_profile_url
    row.committed_at = _parse_commit_datetime(effective_raw)
    row.html_url = effective_raw.get('html_url') or row.html_url
    row.last_synced_at = now

    if isinstance(detail_raw, dict):
        stats = detail_raw.get('stats', {}) or {}
        additions = int(stats.get('additions', 0) or 0)
        deletions = int(stats.get('deletions', 0) or 0)
        total_changes = int(stats.get('total', 0) or (additions + deletions))
        files = detail_raw.get('files', []) or []

        row.additions = additions
        row.deletions = deletions
        row.total_changes = total_changes
        row.changed_files_count = len(files)
        row.files_detail_available = True

        db.session.flush()
        _sync_commit_files(row, files, now)
    elif not row.files_detail_available:
        row.additions = 0
        row.deletions = 0
        row.total_changes = 0
        row.changed_files_count = 0

    return row


def _prune_old_commits(owner: str, repo: str, branch_name: str, now: datetime):
    cutoff = now - timedelta(days=max(_github_retention_days(), 2))
    rows = (
        GitHubCommit.query
        .filter(
            GitHubCommit.owner == owner,
            GitHubCommit.repo == repo,
            GitHubCommit.branch_name == branch_name,
            GitHubCommit.committed_at.isnot(None),
            GitHubCommit.committed_at < cutoff,
        )
        .all()
    )
    for row in rows:
        db.session.delete(row)


def _refresh_commit_cache(owner: str, repo: str, branch_name: str):
    key = _analytics_cache_key(owner, repo, branch_name)
    payload = get_stored_github_24h_commit_details(owner, repo, branch_name)
    if payload is None:
        cache.delete(key)
        return
    cache.set(key, payload, timeout=_github_cache_timeout_seconds())


def _row_to_raw(row: GitHubCommit):
    committed_at = _normalize_datetime(row.committed_at)
    committed_iso = committed_at.isoformat().replace('+00:00', 'Z') if committed_at else None
    return {
        'sha': row.sha,
        'html_url': row.html_url,
        'branch_name': row.branch_name,
        'files_detail_available': bool(row.files_detail_available),
        'commit': {
            'message': row.message,
            'author': {
                'name': row.author_name,
                'date': committed_iso,
            },
            'committer': {
                'name': row.committer_name,
                'date': committed_iso,
            },
        },
        'author': {
            'login': row.author_login,
            'avatar_url': row.author_avatar,
            'html_url': row.author_profile_url,
            'name': row.author_name,
        },
        'committer': {
            'login': row.committer_login,
            'avatar_url': row.committer_avatar,
            'html_url': row.committer_profile_url,
            'name': row.committer_name,
        },
        'stats': (
            {
                'additions': int(row.additions or 0),
                'deletions': int(row.deletions or 0),
                'total': int(row.total_changes or 0),
            }
            if row.files_detail_available
            else {}
        ),
        'files': (
            [
                {
                    'filename': file_row.filename,
                    'previous_filename': file_row.previous_filename,
                    'status': _normalize_file_status(file_row.status),
                    'additions': int(file_row.additions or 0),
                    'deletions': int(file_row.deletions or 0),
                    'changes': int(file_row.changes or 0),
                }
                for file_row in (row.files or [])
            ]
            if row.files_detail_available
            else []
        ),
    }


def get_stored_github_24h_commit_details(owner: str, repo: str, branch_name: str = 'main'):
    now = _utcnow()
    since_dt = now - timedelta(hours=24)
    state = _sync_state(owner, repo, branch_name)

    rows = (
        GitHubCommit.query
        .options(selectinload(GitHubCommit.files))
        .filter(
            GitHubCommit.owner == owner,
            GitHubCommit.repo == repo,
            GitHubCommit.branch_name == branch_name,
            GitHubCommit.committed_at.isnot(None),
            GitHubCommit.committed_at >= since_dt,
        )
        .order_by(GitHubCommit.committed_at.desc(), GitHubCommit.id.desc())
        .all()
    )

    if not rows and (state is None or state.last_synced_at is None):
        return None

    synced_at = _normalize_datetime(state.last_synced_at) if state and state.last_synced_at else None
    commits_raw = [_row_to_raw(row) for row in rows]
    detail_commit_count = sum(1 for item in commits_raw if item.get('files_detail_available'))
    return {
        'source': 'database',
        'branch_name': branch_name,
        'last_synced_at': synced_at.isoformat() if synced_at else None,
        'last_error': state.last_error if state else None,
        'sync_due': _sync_is_due(state),
        'commit_count': len(commits_raw),
        'detail_commit_count': detail_commit_count,
        'missing_detail_count': max(len(commits_raw) - detail_commit_count, 0),
        'commits_raw': commits_raw,
    }


def sync_github_recent_commits(
    owner: str,
    repo: str,
    branch_name: str = 'main',
    *,
    force: bool = False,
):
    state = _sync_state(owner, repo, branch_name)
    if not force and not _sync_is_due(state):
        return False

    app = current_app._get_current_object()
    now = _utcnow()
    since_dt = _sync_since_datetime(state, now)
    sync_state_row = _ensure_sync_state(owner, repo, branch_name)
    sync_state_row.last_attempted_at = now

    try:
        commits = get_commits_for_branch(
            owner,
            repo,
            branch_name,
            per_page=100,
            page_limit=_github_commit_page_limit(),
            since=_datetime_to_github_iso(since_dt),
        ) or []

        shas = [commit.get('sha') for commit in commits if commit.get('sha')]
        existing_rows = _load_existing_commit_rows(owner, repo, shas)
        detail_tasks = {
            sha: (
                lambda commit_sha=sha: _run_in_app_context(
                    app,
                    lambda: get_commit(owner, repo, commit_sha),
                )
            )
            for sha in shas
            if sha not in existing_rows or not existing_rows[sha].files_detail_available
        }
        detail_map = (
            parallel_execute(detail_tasks, max_workers=4, timeout=20)
            if detail_tasks
            else {}
        )
        missing_detail_shas = [
            sha for sha in detail_tasks
            if not isinstance(detail_map.get(sha), dict)
        ]
        for sha in missing_detail_shas:
            detail_map[sha] = _run_in_app_context(
                app,
                lambda commit_sha=sha: get_commit(owner, repo, commit_sha),
            )
        unresolved_detail_shas = [
            sha for sha in detail_tasks
            if not isinstance(detail_map.get(sha), dict)
        ]

        for commit_raw in commits:
            sha = commit_raw.get('sha')
            detail_raw = detail_map.get(sha) if sha else None
            _upsert_commit_row(
                owner,
                repo,
                branch_name,
                commit_raw,
                detail_raw,
                existing_rows,
                now,
            )

        _prune_old_commits(owner, repo, branch_name, now)
        sync_state_row.last_synced_at = now
        if unresolved_detail_shas:
            sync_state_row.last_error = (
                f'File details were unavailable for {len(unresolved_detail_shas)} '
                f'of {len(detail_tasks)} recent commit(s) on the last sync attempt.'
            )
        else:
            sync_state_row.last_error = None
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _record_sync_failure(owner, repo, branch_name, exc)
        current_app.logger.exception(
            'Failed to sync GitHub commits for %s/%s [%s].',
            owner,
            repo,
            branch_name,
        )
        raise

    _refresh_commit_cache(owner, repo, branch_name)
    return True


def _run_async_refresh(app, refresh_key: str, owner: str, repo: str, branch_name: str):
    try:
        with app.app_context():
            sync_github_recent_commits(owner, repo, branch_name, force=True)
    except Exception:
        app.logger.exception(
            'Background GitHub refresh failed for %s/%s [%s].',
            owner,
            repo,
            branch_name,
        )
    finally:
        with _github_refresh_lock:
            _github_refresh_in_progress.discard(refresh_key)


def _start_async_refresh(owner: str, repo: str, branch_name: str):
    refresh_key = _sync_state_key(owner, repo, branch_name)
    with _github_refresh_lock:
        if refresh_key in _github_refresh_in_progress:
            return False
        _github_refresh_in_progress.add(refresh_key)

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_async_refresh,
        args=(app, refresh_key, owner, repo, branch_name),
        daemon=True,
        name=f'github-refresh-{owner}-{repo}-{branch_name}',
    )
    thread.start()
    return True


def get_cached_github_24h_commit_details(owner: str, repo: str, branch_name: str = 'main'):
    key = _analytics_cache_key(owner, repo, branch_name)
    cached = cache.get(key)
    state = _sync_state(owner, repo, branch_name)

    if cached is not None:
        if cached.get('missing_detail_count', 0) > 0 and _detail_retry_due(state):
            sync_github_recent_commits(owner, repo, branch_name, force=True)
            refreshed = get_stored_github_24h_commit_details(owner, repo, branch_name)
            if refreshed is not None:
                cache.set(key, refreshed, timeout=_github_cache_timeout_seconds())
                return refreshed
        if _sync_is_due(state):
            _start_async_refresh(owner, repo, branch_name)
        return cached

    stored = get_stored_github_24h_commit_details(owner, repo, branch_name)
    if stored is not None:
        if stored.get('missing_detail_count', 0) > 0 and _detail_retry_due(state):
            sync_github_recent_commits(owner, repo, branch_name, force=True)
            stored = get_stored_github_24h_commit_details(owner, repo, branch_name) or stored
        cache.set(key, stored, timeout=_github_cache_timeout_seconds())
        if _sync_is_due(state):
            _start_async_refresh(owner, repo, branch_name)
        return stored

    sync_github_recent_commits(owner, repo, branch_name, force=True)
    stored = get_stored_github_24h_commit_details(owner, repo, branch_name)
    if stored is not None:
        cache.set(key, stored, timeout=_github_cache_timeout_seconds())
    return stored
