from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from collectors.jenkins_collector import check_connection
from extensions import cache
from flask import Flask, current_app
from services.alerts_service import get_alerts_payload
from services.azure_service import get_connection_status
from services.deployment_kpis_service import (
    get_deployment_rollout_payload,
    get_deployment_summary_payload,
    merge_deployment_kpis_payload,
)
from services.github_service import invalidate_github_response_cache
from services.github_storage_service import sync_github_recent_commits
from services.jenkins_service import (
    get_live_running_builds,
    refresh_pipeline_storage_from_jenkins,
)
from services.metrics_service import get_cluster_metrics
from services.pipeline_storage_service import get_stored_overview_kpis, get_stored_pipeline_kpis
from services.sonarcloud_service import get_sonarcloud_summary


LIVE_REFRESH_CACHE_TIMEOUT_SECONDS = 300
LIVE_REFRESH_IDLE_SLEEP_SECONDS = 0.1

LIVE_RUNNING_POLL_SECONDS = 1
LIVE_JENKINS_STATUS_POLL_SECONDS = 5
LIVE_AZURE_STATUS_POLL_SECONDS = 15
LIVE_ALERTS_POLL_SECONDS = 1
LIVE_PIPELINE_SNAPSHOT_ACTIVE_SECONDS = 2
LIVE_PIPELINE_SNAPSHOT_IDLE_SECONDS = 5
LIVE_DEPLOYMENT_ROLLOUT_POLL_SECONDS = 10
LIVE_DEPLOYMENT_SUMMARY_POLL_SECONDS = 60
LIVE_DEPLOYMENT_CLUSTER_METRICS_POLL_SECONDS = 20
LIVE_GITHUB_STORAGE_POLL_SECONDS = 30
LIVE_SONARCLOUD_POLL_SECONDS = 30

_DASHBOARD_STATE_CACHE_KEY = 'live_refresh:dashboard_state:v1'
_ALERTS_STATE_CACHE_KEY = 'live_refresh:alerts_state:v1'
_DEPLOYMENT_STATE_CACHE_KEY = 'live_refresh:deployment_state:v1'
_SONARCLOUD_STATE_CACHE_KEY = 'live_refresh:sonarcloud_state:v1'

_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_registered_app: Flask | None = None

_pipeline_snapshot_refresh_lock = threading.Lock()
_pipeline_snapshot_refresh_thread: threading.Thread | None = None
_pipeline_snapshot_refresh_requested = False
_pipeline_snapshot_refresh_needs_completion_sync = False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=lambda item: str(item))]
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat()
        except TypeError:
            pass
    return str(value)


def _payload_signature(payload: Any) -> str:
    return json.dumps(
        _json_safe(payload),
        sort_keys=True,
        separators=(',', ':'),
    )


def _running_builds_summary(builds):
    return [
        {
            'number': build.get('number'),
            'status': build.get('status'),
            'result': build.get('result'),
            'timestamp': build.get('timestamp', 0),
            'duration_ms': build.get('duration_ms', 0) or 0,
            'duration_seconds': build.get('duration_seconds', 0) or 0,
        }
        for build in (builds or [])
    ]


def _worker_process_is_active(app: Flask | None) -> bool:
    if app is None:
        return False
    if app.config.get('TESTING'):
        return False
    if app.debug:
        return os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    return True


def _cache_get_state(key: str) -> dict[str, Any]:
    payload = cache.get(key)
    if isinstance(payload, dict):
        return payload
    return {}


def _cache_set_state(key: str, payload: dict[str, Any]):
    cache.set(key, payload, timeout=LIVE_REFRESH_CACHE_TIMEOUT_SECONDS)


def _update_dashboard_state(*, changes: dict[str, Any]) -> dict[str, Any]:
    state = _cache_get_state(_DASHBOARD_STATE_CACHE_KEY)
    next_state = dict(state)
    changed = False

    for key, value in changes.items():
        safe_value = _json_safe(value)
        if _payload_signature(next_state.get(key)) != _payload_signature(safe_value):
            next_state[key] = safe_value
            changed = True

    if not state:
        next_state.setdefault('version', 0)
        next_state.setdefault('snapshot_version', 0)
        changed = True

    if changed:
        next_state['version'] = int(next_state.get('version') or 0) + 1
        next_state['generated_at'] = _utcnow_iso()
        _cache_set_state(_DASHBOARD_STATE_CACHE_KEY, next_state)
        return next_state

    return state or next_state


def _update_dashboard_snapshot_meta() -> bool:
    state = _cache_get_state(_DASHBOARD_STATE_CACHE_KEY)
    stored_pipeline = get_stored_pipeline_kpis()
    stored_overview = get_stored_overview_kpis() or {}
    snapshot_signature = _payload_signature(stored_pipeline)
    overview_signature = _payload_signature(stored_overview)
    current_signature = str(state.get('snapshot_signature') or '')
    current_overview_signature = str(state.get('overview_payload_signature') or '')

    snapshot_changed = snapshot_signature != current_signature
    overview_changed = overview_signature != current_overview_signature
    if not snapshot_changed and not overview_changed:
        return False

    changed_at = _utcnow_iso()
    next_state = dict(state)
    if snapshot_changed:
        next_state['snapshot_signature'] = snapshot_signature
        next_state['snapshot_version'] = int(next_state.get('snapshot_version') or 0) + 1
        next_state['snapshot_generated_at'] = changed_at

    if overview_changed:
        next_state['overview_payload'] = _json_safe(stored_overview)
        next_state['overview_payload_signature'] = overview_signature
        next_state['overview_version'] = int(next_state.get('overview_version') or 0) + 1
        next_state['overview_generated_at'] = changed_at

    next_state['version'] = int(next_state.get('version') or 0) + 1
    next_state['generated_at'] = changed_at
    _cache_set_state(_DASHBOARD_STATE_CACHE_KEY, next_state)
    return True


def refresh_dashboard_live_state(*, force: bool = False) -> dict[str, Any]:
    running_with_stages = get_live_running_builds(include_stages=True)
    running_summary = _running_builds_summary(running_with_stages)
    jenkins_status = {'connected': bool(check_connection())}
    azure_status = _json_safe(get_connection_status())

    changes = {
        'running_builds': running_summary,
        'running_stages': running_with_stages,
        'jenkins_status': jenkins_status,
        'azure_status': azure_status,
    }
    if force:
        changes['refreshed_at'] = _utcnow_iso()
    return _update_dashboard_state(changes=changes)


def _refresh_dashboard_running_state() -> dict[str, Any]:
    running_with_stages = get_live_running_builds(include_stages=True)
    return _update_dashboard_state(
        changes={
            'running_builds': _running_builds_summary(running_with_stages),
            'running_stages': running_with_stages,
        }
    )


def _refresh_dashboard_jenkins_status() -> dict[str, Any]:
    return _update_dashboard_state(
        changes={
            'jenkins_status': {'connected': bool(check_connection())},
        }
    )


def _refresh_dashboard_azure_status() -> dict[str, Any]:
    return _update_dashboard_state(
        changes={
            'azure_status': _json_safe(get_connection_status()),
        }
    )


def refresh_github_storage_live_state() -> bool:
    owner = str(current_app.config.get('GITHUB_OWNER') or '').strip()
    repo = str(current_app.config.get('GITHUB_REPO') or '').strip()
    if not owner or not repo:
        return False

    refreshed = sync_github_recent_commits(owner, repo, 'main', force=False)
    if refreshed:
        invalidate_github_response_cache(owner, repo)
    return refreshed


def refresh_alerts_live_state(
    *,
    force: bool = False,
    refresh_pipeline_snapshot: bool = False,
    running_builds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    state = _cache_get_state(_ALERTS_STATE_CACHE_KEY)
    alerts_payload = get_alerts_payload(
        refresh_pipeline_snapshot=refresh_pipeline_snapshot,
        running_builds=running_builds,
    )
    alerts_signature = _payload_signature(alerts_payload)

    if force or alerts_signature != str(state.get('signature') or ''):
        next_state = {
            'payload': _json_safe(alerts_payload),
            'signature': alerts_signature,
            'version': int(state.get('version') or 0) + 1,
            'generated_at': _utcnow_iso(),
        }
        _cache_set_state(_ALERTS_STATE_CACHE_KEY, next_state)
        return next_state

    return state


def get_dashboard_live_state() -> dict[str, Any]:
    ensure_live_refresh_worker_started()
    return _cache_get_state(_DASHBOARD_STATE_CACHE_KEY)


def get_alerts_live_state() -> dict[str, Any]:
    ensure_live_refresh_worker_started()
    return _cache_get_state(_ALERTS_STATE_CACHE_KEY)


def get_cached_alerts_payload() -> dict[str, Any]:
    state = get_alerts_live_state()
    payload = state.get('payload')
    if isinstance(payload, dict):
        return payload

    running_builds = get_dashboard_live_state().get('running_stages') or []
    refreshed = refresh_alerts_live_state(
        force=True,
        refresh_pipeline_snapshot=False,
        running_builds=running_builds,
    )
    payload = refreshed.get('payload')
    return payload if isinstance(payload, dict) else {'connected': False, 'alerts': []}


def refresh_sonarcloud_live_state(*, force: bool = False) -> dict[str, Any]:
    state = _cache_get_state(_SONARCLOUD_STATE_CACHE_KEY)
    payload = get_sonarcloud_summary()
    signature = _payload_signature(payload)

    if force or signature != str(state.get('signature') or ''):
        next_state = {
            'payload': _json_safe(payload),
            'signature': signature,
            'version': int(state.get('version') or 0) + 1,
            'generated_at': _utcnow_iso(),
        }
        _cache_set_state(_SONARCLOUD_STATE_CACHE_KEY, next_state)
        return next_state

    return state


def get_sonarcloud_live_state() -> dict[str, Any]:
    ensure_live_refresh_worker_started()
    return _cache_get_state(_SONARCLOUD_STATE_CACHE_KEY)


def get_cached_sonarcloud_payload() -> dict[str, Any]:
    state = get_sonarcloud_live_state()
    payload = state.get('payload')
    if isinstance(payload, dict):
        return payload

    refreshed = refresh_sonarcloud_live_state(force=True)
    payload = refreshed.get('payload')
    return payload if isinstance(payload, dict) else {'connected': False}


def _update_deployment_state(*, changes: dict[str, Any]) -> dict[str, Any]:
    state = _cache_get_state(_DEPLOYMENT_STATE_CACHE_KEY)
    next_state = dict(state)
    changed = False
    changed_at = _utcnow_iso()

    for key, value in changes.items():
        safe_value = _json_safe(value)
        if _payload_signature(next_state.get(key)) == _payload_signature(safe_value):
            continue

        next_state[key] = safe_value
        changed = True

        if key == 'cluster_metrics':
            next_state['cluster_metrics_version'] = int(next_state.get('cluster_metrics_version') or 0) + 1
            next_state['cluster_metrics_generated_at'] = changed_at

    if not state:
        next_state.setdefault('version', 0)
        next_state.setdefault('deployment_version', 0)
        next_state.setdefault('cluster_metrics_version', 0)

    current_deployment_kpis = state.get('deployment_kpis') if isinstance(state.get('deployment_kpis'), dict) else {}
    next_deployment_kpis = merge_deployment_kpis_payload(
        next_state.get('deployment_rollout'),
        next_state.get('deployment_summary'),
    )
    if _payload_signature(current_deployment_kpis) != _payload_signature(next_deployment_kpis):
        next_state['deployment_kpis'] = next_deployment_kpis
        next_state['deployment_version'] = int(next_state.get('deployment_version') or 0) + 1
        next_state['deployment_generated_at'] = changed_at
        changed = True

    if changed:
        next_state['version'] = int(next_state.get('version') or 0) + 1
        next_state['generated_at'] = changed_at
        _cache_set_state(_DEPLOYMENT_STATE_CACHE_KEY, next_state)
        return next_state

    return state or next_state


def refresh_deployment_rollout_live_state() -> dict[str, Any]:
    return _update_deployment_state(
        changes={
            'deployment_rollout': get_deployment_rollout_payload(),
        }
    )


def refresh_deployment_summary_live_state() -> dict[str, Any]:
    return _update_deployment_state(
        changes={
            'deployment_summary': get_deployment_summary_payload(),
        }
    )


def refresh_deployment_kpis_live_state() -> dict[str, Any]:
    return _update_deployment_state(
        changes={
            'deployment_rollout': get_deployment_rollout_payload(),
            'deployment_summary': get_deployment_summary_payload(),
        }
    )


def refresh_cluster_metrics_live_state() -> dict[str, Any]:
    return _update_deployment_state(
        changes={
            'cluster_metrics': get_cluster_metrics(),
        }
    )


def get_deployment_live_state() -> dict[str, Any]:
    ensure_live_refresh_worker_started()
    return _cache_get_state(_DEPLOYMENT_STATE_CACHE_KEY)


def get_cached_deployment_kpis_payload() -> dict[str, Any]:
    state = get_deployment_live_state()
    payload = state.get('deployment_kpis')
    if isinstance(payload, dict):
        return payload

    refreshed = refresh_deployment_kpis_live_state()
    payload = refreshed.get('deployment_kpis')
    return payload if isinstance(payload, dict) else {'connected': False, 'data': {}}


def get_cached_cluster_metrics_payload() -> dict[str, Any]:
    state = get_deployment_live_state()
    payload = state.get('cluster_metrics')
    if isinstance(payload, dict):
        return payload

    refreshed = refresh_cluster_metrics_live_state()
    payload = refreshed.get('cluster_metrics')
    return payload if isinstance(payload, dict) else {'connected': False}


def live_refresh_worker_running() -> bool:
    return _worker_thread is not None and _worker_thread.is_alive()


def ensure_live_refresh_worker_started():
    global _registered_app

    app = _registered_app
    if app is None or live_refresh_worker_running():
        return
    start_live_refresh_worker(app)


def _refresh_pipeline_snapshot(*, include_quality_metrics: bool, include_quality_backfill: bool) -> bool:
    refresh_pipeline_storage_from_jenkins(
        include_quality_metrics=include_quality_metrics,
        include_quality_backfill=include_quality_backfill,
    )
    return _update_dashboard_snapshot_meta()


def _refresh_pipeline_snapshot_after_completion() -> bool:
    # Publish the finished build into the stored snapshot first so SSE-backed
    # history widgets can update promptly, then backfill slower quality data.
    snapshot_changed = _refresh_pipeline_snapshot(
        include_quality_metrics=False,
        include_quality_backfill=False,
    )
    try:
        _refresh_pipeline_snapshot(
            include_quality_metrics=True,
            include_quality_backfill=True,
        )
    except Exception:
        current_app.logger.exception(
            'Central live refresh failed during completed-build quality sync.'
        )
    return snapshot_changed


def _request_pipeline_snapshot_refresh(
    app: Flask,
    *,
    include_quality_follow_up: bool = False,
) -> bool:
    global _pipeline_snapshot_refresh_thread
    global _pipeline_snapshot_refresh_requested
    global _pipeline_snapshot_refresh_needs_completion_sync

    with _pipeline_snapshot_refresh_lock:
        _pipeline_snapshot_refresh_requested = True
        if include_quality_follow_up:
            _pipeline_snapshot_refresh_needs_completion_sync = True

        if (
            _pipeline_snapshot_refresh_thread is not None
            and _pipeline_snapshot_refresh_thread.is_alive()
        ):
            return False

        _pipeline_snapshot_refresh_thread = threading.Thread(
            target=_run_pipeline_snapshot_refresh_worker,
            args=(app,),
            daemon=True,
            name='pipeline-snapshot-refresh',
        )
        _pipeline_snapshot_refresh_thread.start()
        return True


def _run_pipeline_snapshot_refresh_worker(app: Flask):
    global _pipeline_snapshot_refresh_thread
    global _pipeline_snapshot_refresh_requested
    global _pipeline_snapshot_refresh_needs_completion_sync

    with app.app_context():
        while True:
            with _pipeline_snapshot_refresh_lock:
                if not _pipeline_snapshot_refresh_requested:
                    if _pipeline_snapshot_refresh_thread is threading.current_thread():
                        _pipeline_snapshot_refresh_thread = None
                    return

                include_quality_follow_up = bool(
                    _pipeline_snapshot_refresh_needs_completion_sync
                )
                _pipeline_snapshot_refresh_requested = False
                _pipeline_snapshot_refresh_needs_completion_sync = False

            try:
                snapshot_changed = (
                    _refresh_pipeline_snapshot_after_completion()
                    if include_quality_follow_up
                    else _refresh_pipeline_snapshot(
                        include_quality_metrics=False,
                        include_quality_backfill=False,
                    )
                )
            except Exception:
                app.logger.exception(
                    'Central live refresh failed while updating pipeline snapshot.'
                )
                continue

            if not snapshot_changed and not include_quality_follow_up:
                continue

            try:
                running_builds = (
                    _cache_get_state(_DASHBOARD_STATE_CACHE_KEY).get('running_stages') or []
                )
                refresh_alerts_live_state(
                    force=False,
                    refresh_pipeline_snapshot=False,
                    running_builds=running_builds,
                )
            except Exception:
                app.logger.exception(
                    'Central live refresh failed while updating alerts after the pipeline snapshot sync.'
                )


def _run_live_refresh_worker(app: Flask):
    last_running_build_numbers = set()

    next_running_at = 0.0
    next_jenkins_status_at = 0.0
    next_azure_status_at = 0.0
    next_alerts_at = 0.0
    next_pipeline_snapshot_at = 0.0
    next_deployment_rollout_at = 0.0
    next_deployment_summary_at = 0.0
    next_cluster_metrics_at = 0.0
    next_github_storage_at = 0.0
    next_sonarcloud_at = 0.0

    with app.app_context():
        while True:
            now = time.monotonic()
            did_work = False

            if now >= next_running_at:
                try:
                    state = _refresh_dashboard_running_state()
                    running_builds = state.get('running_stages') or []
                    current_running_build_numbers = {
                        int(build.get('number'))
                        for build in (state.get('running_builds') or [])
                        if build.get('number') is not None
                    }
                    running_changed = current_running_build_numbers != last_running_build_numbers
                    finished_builds = last_running_build_numbers - current_running_build_numbers
                    if running_changed:
                        next_pipeline_snapshot_at = 0.0
                        next_alerts_at = 0.0

                    last_running_build_numbers = current_running_build_numbers

                    if finished_builds:
                        try:
                            _request_pipeline_snapshot_refresh(
                                app,
                                include_quality_follow_up=True,
                            )
                        except Exception:
                            app.logger.exception(
                                'Central live refresh failed during completed-build snapshot sync.'
                            )
                        else:
                            next_alerts_at = 0.0
                            next_deployment_summary_at = 0.0

                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating running builds.')
                next_running_at = now + LIVE_RUNNING_POLL_SECONDS

            if now >= next_jenkins_status_at:
                try:
                    _refresh_dashboard_jenkins_status()
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating Jenkins status.')
                next_jenkins_status_at = now + LIVE_JENKINS_STATUS_POLL_SECONDS

            if now >= next_azure_status_at:
                try:
                    _refresh_dashboard_azure_status()
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating Azure status.')
                next_azure_status_at = now + LIVE_AZURE_STATUS_POLL_SECONDS

            if now >= next_pipeline_snapshot_at:
                snapshot_interval_seconds = (
                    LIVE_PIPELINE_SNAPSHOT_ACTIVE_SECONDS
                    if last_running_build_numbers
                    else LIVE_PIPELINE_SNAPSHOT_IDLE_SECONDS
                )
                try:
                    _request_pipeline_snapshot_refresh(
                        app,
                        include_quality_follow_up=False,
                    )
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating pipeline snapshot.')
                next_pipeline_snapshot_at = now + snapshot_interval_seconds

            if now >= next_alerts_at:
                try:
                    running_builds = (
                        _cache_get_state(_DASHBOARD_STATE_CACHE_KEY).get('running_stages') or []
                    )
                    refresh_alerts_live_state(
                        force=False,
                        refresh_pipeline_snapshot=False,
                        running_builds=running_builds,
                    )
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating alerts.')
                next_alerts_at = now + LIVE_ALERTS_POLL_SECONDS

            if now >= next_deployment_rollout_at:
                try:
                    refresh_deployment_rollout_live_state()
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating deployment rollout state.')
                next_deployment_rollout_at = now + LIVE_DEPLOYMENT_ROLLOUT_POLL_SECONDS

            if now >= next_deployment_summary_at:
                try:
                    refresh_deployment_summary_live_state()
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating deployment summary KPIs.')
                next_deployment_summary_at = now + LIVE_DEPLOYMENT_SUMMARY_POLL_SECONDS

            if now >= next_cluster_metrics_at:
                try:
                    refresh_cluster_metrics_live_state()
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating deployment cluster metrics.')
                next_cluster_metrics_at = now + LIVE_DEPLOYMENT_CLUSTER_METRICS_POLL_SECONDS

            if now >= next_github_storage_at:
                try:
                    refresh_github_storage_live_state()
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating GitHub storage.')
                next_github_storage_at = now + LIVE_GITHUB_STORAGE_POLL_SECONDS

            if now >= next_sonarcloud_at:
                try:
                    refresh_sonarcloud_live_state()
                    did_work = True
                except Exception:
                    app.logger.exception('Central live refresh failed while updating SonarCloud state.')
                next_sonarcloud_at = now + LIVE_SONARCLOUD_POLL_SECONDS

            if not did_work:
                time.sleep(LIVE_REFRESH_IDLE_SLEEP_SECONDS)


def start_live_refresh_worker(app: Flask):
    global _registered_app, _worker_thread

    _registered_app = app
    if not _worker_process_is_active(app):
        return False

    with _worker_lock:
        if live_refresh_worker_running():
            return True

        worker = threading.Thread(
            target=_run_live_refresh_worker,
            args=(app,),
            daemon=True,
            name='live-refresh-worker',
        )
        worker.start()
        _worker_thread = worker
        return True
