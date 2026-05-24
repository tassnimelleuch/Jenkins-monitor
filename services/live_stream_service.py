from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from collectors.jenkins_collector import get_console_log
from extensions import cache
from flask import current_app
from services.azure_service import get_connection_status
from services.jenkins_service import get_live_running_builds


LIVE_STREAM_RETRY_MS = 3000
LIVE_RUNNING_POLL_SECONDS = 2
LIVE_JENKINS_STATUS_POLL_SECONDS = 10
LIVE_AZURE_STATUS_POLL_SECONDS = 20
LIVE_ALERTS_POLL_SECONDS = 10
LIVE_HEARTBEAT_SECONDS = 15

CONSOLE_LOG_POLL_SECONDS = 2
CONSOLE_HEARTBEAT_SECONDS = 15

JENKINS_STATUS_CACHE_TTL_SECONDS = 5
AZURE_STATUS_CACHE_TTL_SECONDS = 15

_JENKINS_STATUS_CACHE_KEY = 'live_status:jenkins:v1'
_AZURE_STATUS_CACHE_KEY = 'live_status:azure:v1'


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
    safe_payload = _json_safe(payload)
    return json.dumps(
        safe_payload,
        sort_keys=True,
        separators=(',', ':'),
    )


def _format_sse_event(event_name: str, payload: Any, *, retry_ms: int | None = None) -> str:
    safe_payload = _json_safe(payload)
    lines = []
    if retry_ms is not None:
        lines.append(f'retry: {int(retry_ms)}')
    lines.append(f'event: {event_name}')
    for line in json.dumps(safe_payload, separators=(',', ':')).splitlines():
        lines.append(f'data: {line}')
    lines.append('')
    return '\n'.join(lines) + '\n'


def _format_sse_comment(comment: str) -> str:
    return f': {comment}\n\n'


def _log_stream_exception(message: str):
    try:
        current_app.logger.exception(message)
    except Exception:
        return


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


def get_cached_jenkins_status_payload():
    cached = cache.get(_JENKINS_STATUS_CACHE_KEY)
    if cached is not None:
        return cached

    payload = {'connected': bool(get_live_jenkins_connection())}
    cache.set(
        _JENKINS_STATUS_CACHE_KEY,
        payload,
        timeout=JENKINS_STATUS_CACHE_TTL_SECONDS,
    )
    return payload


def get_cached_azure_status_payload():
    cached = cache.get(_AZURE_STATUS_CACHE_KEY)
    if cached is not None:
        return cached

    payload = _json_safe(get_connection_status())
    cache.set(
        _AZURE_STATUS_CACHE_KEY,
        payload,
        timeout=AZURE_STATUS_CACHE_TTL_SECONDS,
    )
    return payload


def get_live_jenkins_connection() -> bool:
    from collectors.jenkins_collector import check_connection

    return check_connection()


def iter_dashboard_live_events():
    last_running_summary_signature = None
    last_running_stages_signature = None
    last_jenkins_status_signature = None
    last_azure_status_signature = None
    last_running_build_numbers = set()
    has_seen_running_state = False

    next_running_at = 0.0
    next_jenkins_status_at = 0.0
    next_azure_status_at = 0.0
    next_heartbeat_at = 0.0

    try:
        yield _format_sse_event(
            'stream_ready',
            {'ts': _utcnow_iso()},
            retry_ms=LIVE_STREAM_RETRY_MS,
        )

        while True:
            now = time.monotonic()
            emitted = False

            if now >= next_running_at:
                try:
                    running_with_stages = get_live_running_builds(include_stages=True)
                    running_summary = _running_builds_summary(running_with_stages)

                    running_summary_signature = _payload_signature(running_summary)
                    if running_summary_signature != last_running_summary_signature:
                        last_running_summary_signature = running_summary_signature
                        emitted = True
                        yield _format_sse_event(
                            'running_builds',
                            {
                                'builds': running_summary,
                                'generated_at': _utcnow_iso(),
                            },
                            retry_ms=LIVE_STREAM_RETRY_MS,
                        )

                    running_stages_signature = _payload_signature(running_with_stages)
                    if running_stages_signature != last_running_stages_signature:
                        last_running_stages_signature = running_stages_signature
                        emitted = True
                        yield _format_sse_event(
                            'running_stages',
                            {
                                'builds': _json_safe(running_with_stages),
                                'generated_at': _utcnow_iso(),
                            }
                        )

                    current_running_build_numbers = {
                        int(build.get('number'))
                        for build in (running_with_stages or [])
                        if build.get('number') is not None
                    }
                    started = sorted(current_running_build_numbers - last_running_build_numbers)
                    finished = sorted(last_running_build_numbers - current_running_build_numbers)

                    if has_seen_running_state and started:
                        emitted = True
                        yield _format_sse_event(
                            'build_started',
                            {
                                'build_numbers': started,
                                'generated_at': _utcnow_iso(),
                            }
                        )
                    if has_seen_running_state and finished:
                        emitted = True
                        yield _format_sse_event(
                            'build_finished',
                            {
                                'build_numbers': finished,
                                'generated_at': _utcnow_iso(),
                            }
                        )

                    last_running_build_numbers = current_running_build_numbers
                    has_seen_running_state = True
                except Exception:
                    emitted = True
                    _log_stream_exception('Dashboard live stream running-build update failed.')
                    yield _format_sse_comment(f'running-update-error {_utcnow_iso()}')
                next_running_at = now + LIVE_RUNNING_POLL_SECONDS

            if now >= next_jenkins_status_at:
                try:
                    jenkins_status = get_cached_jenkins_status_payload()
                    jenkins_status_signature = _payload_signature(jenkins_status)
                    if jenkins_status_signature != last_jenkins_status_signature:
                        last_jenkins_status_signature = jenkins_status_signature
                        emitted = True
                        yield _format_sse_event('jenkins_status', jenkins_status)
                except Exception:
                    emitted = True
                    _log_stream_exception('Dashboard live stream Jenkins status update failed.')
                    yield _format_sse_comment(f'jenkins-status-error {_utcnow_iso()}')
                next_jenkins_status_at = now + LIVE_JENKINS_STATUS_POLL_SECONDS

            if now >= next_azure_status_at:
                try:
                    azure_status = get_cached_azure_status_payload()
                    azure_status_signature = _payload_signature(azure_status)
                    if azure_status_signature != last_azure_status_signature:
                        last_azure_status_signature = azure_status_signature
                        emitted = True
                        yield _format_sse_event('azure_status', azure_status)
                except Exception:
                    emitted = True
                    _log_stream_exception('Dashboard live stream Azure status update failed.')
                    yield _format_sse_comment(f'azure-status-error {_utcnow_iso()}')
                next_azure_status_at = now + LIVE_AZURE_STATUS_POLL_SECONDS

            if now >= next_heartbeat_at:
                emitted = True
                yield _format_sse_event(
                    'heartbeat',
                    {
                        'ts': _utcnow_iso(),
                    }
                )
                next_heartbeat_at = now + LIVE_HEARTBEAT_SECONDS

            if not emitted:
                time.sleep(0.25)
    except GeneratorExit:
        return


def iter_alert_live_events():
    from services.alerts_service import get_alerts_payload

    last_alerts_signature = None
    next_alerts_at = 0.0
    next_heartbeat_at = 0.0

    try:
        yield _format_sse_event(
            'stream_ready',
            {'ts': _utcnow_iso()},
            retry_ms=LIVE_STREAM_RETRY_MS,
        )

        while True:
            now = time.monotonic()
            emitted = False

            if now >= next_alerts_at:
                try:
                    alerts_payload = get_alerts_payload()
                    alerts_signature = _payload_signature(alerts_payload)
                    if alerts_signature != last_alerts_signature:
                        last_alerts_signature = alerts_signature
                        emitted = True
                        yield _format_sse_event(
                            'alerts_payload',
                            alerts_payload,
                            retry_ms=LIVE_STREAM_RETRY_MS,
                        )
                except Exception:
                    emitted = True
                    _log_stream_exception('Alerts live stream update failed.')
                    yield _format_sse_comment(f'alerts-update-error {_utcnow_iso()}')
                next_alerts_at = now + LIVE_ALERTS_POLL_SECONDS

            if now >= next_heartbeat_at:
                emitted = True
                yield _format_sse_event(
                    'heartbeat',
                    {'ts': _utcnow_iso()},
                )
                next_heartbeat_at = now + LIVE_HEARTBEAT_SECONDS

            if not emitted:
                time.sleep(0.25)
    except GeneratorExit:
        return


def iter_console_log_events(build_number: int):
    last_log_signature = None
    next_log_at = 0.0
    next_heartbeat_at = 0.0

    try:
        yield _format_sse_event(
            'stream_ready',
            {
                'build_number': build_number,
                'ts': _utcnow_iso(),
            },
            retry_ms=LIVE_STREAM_RETRY_MS,
        )

        while True:
            now = time.monotonic()
            emitted = False

            if now >= next_log_at:
                try:
                    log_text = get_console_log(build_number)
                    log_signature = _payload_signature(log_text)
                    if log_signature != last_log_signature:
                        last_log_signature = log_signature
                        emitted = True
                        yield _format_sse_event(
                            'log_snapshot',
                            {
                                'build_number': build_number,
                                'log': log_text or '',
                                'generated_at': _utcnow_iso(),
                            },
                            retry_ms=LIVE_STREAM_RETRY_MS,
                        )

                        upper_log = str(log_text or '').upper()
                        if 'FINISHED: SUCCESS' in upper_log:
                            yield _format_sse_event('build_result', {'build_number': build_number, 'result': 'SUCCESS'})
                            return
                        if 'FINISHED: FAILURE' in upper_log:
                            yield _format_sse_event('build_result', {'build_number': build_number, 'result': 'FAILURE'})
                            return
                        if 'FINISHED: ABORTED' in upper_log:
                            yield _format_sse_event('build_result', {'build_number': build_number, 'result': 'ABORTED'})
                            return
                except Exception:
                    emitted = True
                    _log_stream_exception(f'Console live stream update failed for build #{build_number}.')
                    yield _format_sse_comment(f'console-log-error {_utcnow_iso()}')

                next_log_at = now + CONSOLE_LOG_POLL_SECONDS

            if now >= next_heartbeat_at:
                emitted = True
                yield _format_sse_comment(f'heartbeat {_utcnow_iso()}')
                next_heartbeat_at = now + CONSOLE_HEARTBEAT_SECONDS

            if not emitted:
                time.sleep(0.25)
    except GeneratorExit:
        return
