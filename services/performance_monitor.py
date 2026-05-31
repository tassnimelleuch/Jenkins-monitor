from __future__ import annotations

from contextlib import contextmanager
import json
import re
from time import perf_counter
from typing import Any

from flask import current_app, g, has_app_context, has_request_context, request


MAX_PHASES_IN_RESPONSE = 6
MAX_PARALLEL_GROUPS_IN_RESPONSE = 4
DEFAULT_EXECUTION_MODE = 'parallel'
SEQUENTIAL_EXECUTION_MODE = 'sequential'
PARALLEL_EXECUTION_MODE = 'parallel'


def _round_ms(value: float) -> float:
    return round(max(float(value), 0.0), 1)


def _sanitize_metric_name(name: str) -> str:
    sanitized = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(name or 'phase').strip().lower())
    sanitized = sanitized.strip('-_')
    return sanitized or 'phase'


def _get_request_perf() -> dict[str, Any] | None:
    if not has_request_context():
        return None
    return getattr(g, '_request_perf', None)


def normalize_execution_mode(value: Any) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in ('serial', 'sync', 'single', 'single-thread', 'single_thread'):
        return SEQUENTIAL_EXECUTION_MODE
    if normalized == SEQUENTIAL_EXECUTION_MODE:
        return SEQUENTIAL_EXECUTION_MODE
    return PARALLEL_EXECUTION_MODE


def get_parallel_execution_mode() -> str:
    if has_app_context() and current_app.config.get('JM_FORCE_SEQUENTIAL'):
        return SEQUENTIAL_EXECUTION_MODE

    if (
        has_request_context()
        and (not has_app_context() or current_app.config.get('JM_ALLOW_REQUEST_EXECUTION_MODE_OVERRIDE', True))
    ):
        requested_mode = (
            request.headers.get('X-JM-Execution-Mode')
            or request.args.get('execution')
            or request.args.get('exec')
        )
        if requested_mode:
            return normalize_execution_mode(requested_mode)

    if has_app_context():
        return normalize_execution_mode(
            current_app.config.get('JM_PARALLEL_EXECUTION_MODE', DEFAULT_EXECUTION_MODE)
        )

    return DEFAULT_EXECUTION_MODE


def start_api_request_timer() -> None:
    if not has_request_context() or not request.path.startswith('/api/'):
        return
    if _get_request_perf() is not None:
        return

    g._request_perf = {
        'started_at': perf_counter(),
        'method': request.method,
        'path': request.path,
        'execution_mode': get_parallel_execution_mode(),
        'phases': [],
        'parallel_groups': [],
    }


def record_phase(name: str, duration_ms: float, description: str | None = None) -> None:
    request_perf = _get_request_perf()
    if request_perf is None:
        return

    request_perf['phases'].append({
        'name': str(name or 'phase'),
        'metric_name': _sanitize_metric_name(name),
        'duration_ms': _round_ms(duration_ms),
        'description': description,
    })


@contextmanager
def track_phase(name: str, description: str | None = None):
    started_at = perf_counter()
    try:
        yield
    finally:
        record_phase(name, (perf_counter() - started_at) * 1000, description=description)


def record_parallel_group(
    name: str,
    *,
    execution_mode: str,
    total_ms: float,
    sequential_estimate_ms: float,
    task_count: int,
    completed_task_count: int,
    failed_task_count: int,
    max_workers: int,
    timeout: int,
) -> None:
    request_perf = _get_request_perf()
    if request_perf is None:
        return

    total_ms = _round_ms(total_ms)
    sequential_estimate_ms = _round_ms(sequential_estimate_ms)
    request_perf['parallel_groups'].append({
        'name': str(name or 'parallel'),
        'metric_name': _sanitize_metric_name(name),
        'execution_mode': normalize_execution_mode(execution_mode),
        'total_ms': total_ms,
        'sequential_estimate_ms': sequential_estimate_ms,
        'saved_ms': _round_ms(sequential_estimate_ms - total_ms),
        'task_count': int(task_count),
        'completed_task_count': int(completed_task_count),
        'failed_task_count': int(failed_task_count),
        'max_workers': int(max_workers),
        'timeout_seconds': int(timeout),
    })


def _build_phase_summary(phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phase_totals: dict[str, dict[str, Any]] = {}

    for phase in phases:
        metric_name = phase['metric_name']
        current = phase_totals.get(metric_name)
        if current is None:
            phase_totals[metric_name] = {
                'name': phase['name'],
                'metric_name': metric_name,
                'duration_ms': float(phase['duration_ms']),
            }
            continue
        current['duration_ms'] += float(phase['duration_ms'])

    return [
        {
            **phase,
            'duration_ms': _round_ms(phase['duration_ms']),
        }
        for phase in sorted(
            phase_totals.values(),
            key=lambda item: item['duration_ms'],
            reverse=True,
        )[:MAX_PHASES_IN_RESPONSE]
    ]


def _build_parallel_group_summary(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return groups[:MAX_PARALLEL_GROUPS_IN_RESPONSE]


def _build_perf_summary(request_perf: dict[str, Any], total_ms: float) -> dict[str, Any]:
    phases = _build_phase_summary(request_perf.get('phases') or [])
    all_parallel_groups = request_perf.get('parallel_groups') or []
    parallel_groups = _build_parallel_group_summary(all_parallel_groups)
    parallel_wall_ms = sum(group['total_ms'] for group in all_parallel_groups)
    parallel_sequential_ms = sum(group['sequential_estimate_ms'] for group in all_parallel_groups)

    return {
        'method': request_perf.get('method'),
        'path': request_perf.get('path'),
        'execution_mode': normalize_execution_mode(request_perf.get('execution_mode')),
        'total_ms': _round_ms(total_ms),
        'backend_ms': _round_ms(total_ms),
        'parallel_wall_ms': _round_ms(parallel_wall_ms),
        'parallel_sequential_ms': _round_ms(parallel_sequential_ms),
        'parallel_saved_ms': _round_ms(parallel_sequential_ms - parallel_wall_ms),
        'parallel_group_count': len(all_parallel_groups),
        'phases': phases,
        'parallel_groups': parallel_groups,
    }


def _build_server_timing(summary: dict[str, Any]) -> str:
    metrics = [f"total;dur={summary['total_ms']:.1f}"]

    for phase in summary.get('phases') or []:
        desc = str(phase.get('name') or phase.get('metric_name') or 'phase').replace('"', "'")
        metrics.append(
            f"{phase['metric_name']};dur={float(phase['duration_ms']):.1f};desc=\"{desc}\""
        )

    if summary.get('parallel_wall_ms'):
        metrics.append(f"parallel;dur={float(summary['parallel_wall_ms']):.1f};desc=\"parallel wall\"")
        metrics.append(
            f"parallel-seq;dur={float(summary['parallel_sequential_ms']):.1f};"
            'desc="sequential estimate"'
        )
        metrics.append(
            f"parallel-saved;dur={float(summary['parallel_saved_ms']):.1f};desc=\"estimated saved\""
        )

    return ', '.join(metrics)


def finalize_api_request_timer(response):
    request_perf = _get_request_perf()
    if request_perf is None:
        return response
    if response.mimetype == 'text/event-stream':
        return response

    total_ms = (perf_counter() - request_perf['started_at']) * 1000
    summary = _build_perf_summary(request_perf, total_ms)

    response.headers['X-Backend-Duration-Ms'] = f"{summary['total_ms']:.1f}"
    response.headers['X-JM-Execution-Mode'] = summary['execution_mode']
    response.headers['X-JM-Performance'] = json.dumps(summary, separators=(',', ':'))

    if summary.get('parallel_wall_ms'):
        response.headers['X-Parallel-Wall-Ms'] = f"{summary['parallel_wall_ms']:.1f}"
        response.headers['X-Parallel-Sequential-Ms'] = f"{summary['parallel_sequential_ms']:.1f}"
        response.headers['X-Parallel-Saved-Ms'] = f"{summary['parallel_saved_ms']:.1f}"

    server_timing = _build_server_timing(summary)
    existing_server_timing = response.headers.get('Server-Timing')
    response.headers['Server-Timing'] = (
        f'{existing_server_timing}, {server_timing}'
        if existing_server_timing
        else server_timing
    )
    return response
