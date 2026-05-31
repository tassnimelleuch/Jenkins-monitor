from __future__ import annotations

from flask import current_app

from extensions import cache
from pipeline_identity import configured_branch_name, pipeline_name
from services.background_refresh_service import (
    get_deployment_live_state,
    get_sonarcloud_live_state,
)
from services.finops_storage_service import get_cached_stored_daily_cost_chart
from services.github_service import _github_summary_cache_key
from services.github_storage_service import get_cached_github_24h_commit_details
from services.jenkins_service import get_pipeline_kpis
from services.parallel_executor import parallel_execute
from services.pipeline_storage_service import get_stored_pipeline_kpis
from services.system_timezone_service import now_system_timezone


MAIN_BRANCH_NAME = 'main'


def _run_in_app_context(app, func):
    with app.app_context():
        return func()


def _safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _optional_int(value):
    try:
        if value in (None, ''):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value, default=None):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def _build_file_name(exported_at):
    return exported_at.strftime('jenkins-monitor-report-%Y%m%d-%H%M%S.pdf')


def _normalize_build_status(build):
    build = build or {}

    result = str(build.get('result') or '').strip().upper()
    if result:
        return result

    status = str(build.get('status') or '').strip().upper()
    if status in ('IN_PROGRESS', 'BUILDING', 'RUNNING'):
        return 'RUNNING'
    if status:
        return status.replace('_', ' ')

    return 'UNKNOWN'


def _selected_branch_payload(payload):
    payload = payload or {}
    pipeline_payload = payload.get('pipeline') or {}
    selected_branch = pipeline_payload.get('selected_branch') or configured_branch_name(
        current_app.config,
        default=MAIN_BRANCH_NAME,
    )
    branches = payload.get('branches') or {}
    return selected_branch, (branches.get(selected_branch) or {})


def _load_pipeline_snapshot():
    payload = get_stored_pipeline_kpis() or get_pipeline_kpis()
    if not payload or not payload.get('connected'):
        return {
            'connected': False,
            'message': 'Unable to fetch Jenkins pipeline data.',
        }

    selected_branch, branch_payload = _selected_branch_payload(payload)
    summary = branch_payload.get('summary') or {}
    quality = branch_payload.get('quality') or {}
    latest_build = branch_payload.get('last_build') or branch_payload.get('last_completed_build') or {}

    return {
        'connected': True,
        'message': None,
        'pipeline_name': (payload.get('pipeline') or {}).get('name') or pipeline_name(
            current_app.config.get('JENKINS_JOB'),
            branch_name=selected_branch,
        ),
        'branch_name': selected_branch,
        'latest_build': {
            'number': latest_build.get('number') or summary.get('last_build_number'),
            'status': _normalize_build_status(latest_build),
            'result': latest_build.get('result'),
            'timestamp': latest_build.get('timestamp'),
        },
        'average_duration_ms': summary.get('avg_duration_ms'),
        'average_test_coverage': quality.get('avg_test_coverage'),
        'success_rate': summary.get('success_rate'),
        'jenkins_health_score': summary.get('health_score'),
    }


def _load_finops_snapshot(exported_at):
    subscription_id = current_app.config.get('AZURE_SUBSCRIPTION_ID')
    if not subscription_id:
        return {
            'connected': False,
            'message': 'Azure subscription is not configured.',
        }

    chart = get_cached_stored_daily_cost_chart(
        subscription_id,
        exported_at.year,
        exported_at.month,
    )
    if not chart:
        return {
            'connected': False,
            'message': 'Stored Azure cost data is not available yet.',
        }

    labels = chart.get('labels') or []
    totals = ((chart.get('series') or {}).get('total') or [])
    daily_map = {
        label: _safe_float(totals[index], default=0.0) or 0.0
        for index, label in enumerate(labels)
        if index < len(totals)
    }

    snapshot_day = exported_at.date().isoformat()
    summary = chart.get('summary') or {}

    return {
        'connected': True,
        'snapshot_day': snapshot_day,
        'currency_code': 'USD',
        'day_cost': _safe_float(daily_map.get(snapshot_day), default=0.0) or 0.0,
        'month_total_cost': _safe_float(summary.get('total_cost'), default=0.0) or 0.0,
    }


def _format_commit(commit_raw, branch_name=MAIN_BRANCH_NAME):
    commit_raw = commit_raw or {}
    commit = commit_raw.get('commit', {}) or {}
    author = commit.get('author', {}) or {}
    committer = commit.get('committer', {}) or {}
    author_user = commit_raw.get('author') or {}

    message = (commit.get('message') or '').strip()
    headline = message.splitlines()[0] if message else None
    sha = commit_raw.get('sha')

    return {
        'branch_name': branch_name,
        'sha': sha,
        'short_sha': sha[:7] if sha else None,
        'message': message,
        'headline': headline,
        'author_name': author.get('name') or author_user.get('login'),
        'date': author.get('date') or committer.get('date'),
        'url': commit_raw.get('html_url'),
    }


def _latest_commit_from_analytics_payload(analytics_payload):
    commits_raw = analytics_payload.get('commits_raw') or []
    dated_items = []

    for commit_raw in commits_raw:
        if not isinstance(commit_raw, dict):
            continue
        commit = commit_raw.get('commit', {}) or {}
        author = commit.get('author', {}) or {}
        committer = commit.get('committer', {}) or {}
        commit_date = author.get('date') or committer.get('date')
        dated_items.append((commit_date or '', commit_raw))

    if not dated_items:
        return None

    dated_items.sort(key=lambda item: item[0], reverse=True)
    return dated_items[0][1]


def _top_changed_file_from_commits(commits_raw):
    if not commits_raw:
        return None

    file_changes = {}
    for commit in commits_raw:
        if not isinstance(commit, dict):
            continue

        for file_obj in (commit.get('files') or []):
            filename = str((file_obj or {}).get('filename') or '').strip()
            if not filename:
                continue

            entry = file_changes.setdefault(filename, {
                'filename': filename,
                'touches': 0,
                'additions': 0,
                'deletions': 0,
                'line_changes': 0,
            })
            entry['touches'] += 1
            entry['additions'] += _safe_int((file_obj or {}).get('additions'), default=0)
            entry['deletions'] += _safe_int((file_obj or {}).get('deletions'), default=0)

    if not file_changes:
        return None

    for entry in file_changes.values():
        entry['line_changes'] = entry['additions'] + entry['deletions']

    ranked = sorted(
        file_changes.values(),
        key=lambda item: (-item['line_changes'], -item['touches'], item['filename']),
    )
    return ranked[0]


def _load_github_snapshot():
    owner = current_app.config.get('GITHUB_OWNER')
    repo = current_app.config.get('GITHUB_REPO')
    if not owner or not repo:
        return {
            'connected': False,
            'message': 'GitHub repository is not configured.',
        }

    analytics_payload = get_cached_github_24h_commit_details(owner, repo, MAIN_BRANCH_NAME) or {}
    cached_summary = cache.get(_github_summary_cache_key(owner, repo)) or {}
    pull_requests_open = cached_summary.get('pull_requests_open')
    open_pr_count = len(pull_requests_open) if isinstance(pull_requests_open, list) else None
    latest_main_commit_raw = _latest_commit_from_analytics_payload(analytics_payload)
    top_changed_file = _top_changed_file_from_commits(analytics_payload.get('commits_raw') or [])
    has_cached_data = bool(latest_main_commit_raw or top_changed_file or open_pr_count is not None)

    return {
        'connected': has_cached_data,
        'owner': owner,
        'repo': repo,
        'open_pr_count': open_pr_count,
        'main_commit': (
            _format_commit(latest_main_commit_raw, branch_name=MAIN_BRANCH_NAME)
            if latest_main_commit_raw else None
        ),
        'top_changed_file': top_changed_file,
        'message': None if has_cached_data else 'Stored GitHub export data is not available yet.',
    }


def _load_sonar_snapshot():
    state = get_sonarcloud_live_state()
    summary = state.get('payload') if isinstance(state, dict) else None
    if not isinstance(summary, dict):
        return {
            'connected': False,
            'message': 'Cached SonarCloud data is not available yet.',
        }

    if not summary.get('connected'):
        return {
            'connected': False,
            'message': summary.get('message') or 'Unable to fetch SonarCloud data.',
        }

    metrics = summary.get('metrics') or {}
    bug_counts = metrics.get('bugs') or {}
    bugs_total = sum(_safe_int(count, default=0) for count in bug_counts.values())
    vulnerabilities = _safe_int(metrics.get('vulnerabilities'), default=0)
    code_smells = _safe_int(metrics.get('code_smells'), default=0)
    security_hotspots = _safe_int(metrics.get('security_hotspots'), default=0)

    return {
        'connected': True,
        'total_issues': bugs_total + vulnerabilities + code_smells + security_hotspots,
        'bugs_total': bugs_total,
        'code_smells': code_smells,
        'vulnerabilities': vulnerabilities,
        'security_hotspots': security_hotspots,
    }


def _load_cluster_and_docker_snapshot():
    state = get_deployment_live_state()
    result = state.get('deployment_kpis') if isinstance(state, dict) else None
    if not isinstance(result, dict):
        return {
            'connected': False,
            'message': 'Cached cluster data is not available yet.',
        }

    if not result.get('connected'):
        return {
            'connected': False,
            'message': result.get('message') or 'Unable to fetch cluster data.',
        }

    data = result.get('data') or {}
    pods_by_phase = data.get('pods_by_phase') or {}
    latest_image = data.get('latest_image') or {}
    warnings = []

    pods_running = _optional_int(pods_by_phase.get('Running'))
    pods_total = _optional_int(data.get('pods_total'))
    image_build_number = _optional_int(latest_image.get('build_number'))

    if pods_total is None:
        warnings.append('Kubernetes pod snapshot is unavailable.')
    if not latest_image.get('tag'):
        warnings.append('Latest Docker image metadata is unavailable.')

    return {
        'connected': True,
        'message': ' '.join(warnings) if warnings else None,
        'pods_running': pods_running,
        'pods_total': pods_total,
        'last_docker_image_tag': latest_image.get('tag'),
        'last_docker_image_name': latest_image.get('image_name'),
        'last_docker_image_build_number': image_build_number,
    }


def _warning_for(section_name, payload):
    if not isinstance(payload, dict):
        return f'{section_name} data is unavailable.'

    message = (payload.get('message') or '').strip()
    if message:
        return f'{section_name}: {message}'

    if payload.get('connected') is False:
        return f'{section_name} data is unavailable.'

    return None


def get_pdf_report_snapshot():
    exported_at = now_system_timezone()
    app = current_app._get_current_object()

    tasks = {
        'pipeline': lambda: _run_in_app_context(app, _load_pipeline_snapshot),
        'finops': lambda: _run_in_app_context(app, lambda: _load_finops_snapshot(exported_at)),
        'github': lambda: _run_in_app_context(app, _load_github_snapshot),
        'sonarcloud': lambda: _run_in_app_context(app, _load_sonar_snapshot),
        'cluster': lambda: _run_in_app_context(app, _load_cluster_and_docker_snapshot),
    }
    results = parallel_execute(tasks, max_workers=5, timeout=10)

    pipeline_snapshot = results.get('pipeline') or {}
    cluster_snapshot = results.get('cluster') or {}
    github_snapshot = results.get('github') or {}
    sonar_snapshot = results.get('sonarcloud') or {}
    finops_snapshot = results.get('finops') or {}

    warnings = []
    for section_name, payload in (
        ('Jenkins', pipeline_snapshot),
        ('FinOps', finops_snapshot),
        ('GitHub', github_snapshot),
        ('SonarCloud', sonar_snapshot),
        ('Cluster', cluster_snapshot),
    ):
        warning = _warning_for(section_name, payload)
        if warning:
            warnings.append(warning)

    return {
        'generated_at': exported_at.isoformat(),
        'file_name': _build_file_name(exported_at),
        'pipeline_name': pipeline_snapshot.get('pipeline_name'),
        'branch_name': pipeline_snapshot.get('branch_name'),
        'latest_build': pipeline_snapshot.get('latest_build') or {},
        'average_duration_ms': pipeline_snapshot.get('average_duration_ms'),
        'average_test_coverage': pipeline_snapshot.get('average_test_coverage'),
        'success_rate': pipeline_snapshot.get('success_rate'),
        'jenkins_health_score': pipeline_snapshot.get('jenkins_health_score'),
        'finops': finops_snapshot,
        'github': github_snapshot,
        'sonarqube': sonar_snapshot,
        'kubernetes': {
            'connected': cluster_snapshot.get('connected', False),
            'pods_running': cluster_snapshot.get('pods_running'),
            'pods_total': cluster_snapshot.get('pods_total'),
        },
        'docker': {
            'connected': cluster_snapshot.get('connected', False),
            'tag': cluster_snapshot.get('last_docker_image_tag'),
            'image_name': cluster_snapshot.get('last_docker_image_name'),
            'build_number': cluster_snapshot.get('last_docker_image_build_number'),
        },
        'warnings': warnings,
    }
