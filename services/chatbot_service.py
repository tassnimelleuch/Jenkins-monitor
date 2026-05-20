from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import date

import requests
from flask import current_app

from services.dashboard_kpi_chroma_service import query_dashboard_kpi_chroma
from services.finops_build_documents_service import (
    get_finops_build_document,
    list_finops_build_documents_for_range,
)
from services.finops_chroma_service import query_finops_chroma


DASHBOARD_KPI_RAG_RESULT_LIMIT = 3
DASHBOARD_KPI_METRIC_KEYWORDS = (
    'health score',
    'success rate',
    'active builds',
    'build trend',
    'build history',
    'latest build duration',
    'latest builds duration',
    'test stages duration',
    'tests duration',
    'test coverage',
    'coverage trend',
    'unit test results',
    'junit',
    'failure rate by stage',
    'stage failure rate',
    'top failing stages',
    'cpu by namespace',
    'memory by namespace',
    'network by namespace',
    'disk by namespace',
    'namespace cpu',
    'namespace ram',
    'namespace memory',
    'namespace network',
    'namespace disk',
    'pods by namespace',
    'replicasets by namespace',
    'pvcs by namespace',
    'pods by phase',
    'deployment frequency',
    'pods total',
    'replicasets total',
    'pvcs total',
    'latest image artifact',
    'docker image',
    'docker hub image',
    'image artifact',
    'github repo page',
    'github page',
    'github repository',
    'latest branch commits',
    'open pull requests',
    'merged pull requests',
    'recently merged',
    'last failed main pipeline commit',
    'fix for latest main failure',
    'time to fix',
    'most changed files',
    'code churn',
    'failed pipeline commit',
    'failed by',
    'daily total cost',
    'average / day',
    'highest day',
    'vs previous month',
    'finops total cost',
    'finops average',
    'finops highest day',
    'finops month change',
    'daily cost chart',
    'ecoops',
    'ecoops page',
    'aks co2',
    'vm co2',
    'combined last hour',
    'co2 emission rate',
    'wall power',
    'sonarcloud',
    'sonarqube',
    'quality gate',
    'quality gate conditions',
    'sonar bugs',
    'sonar vulnerabilities',
    'code smells',
    'security hotspots',
    'duplications',
    'duplicated lines density',
    'ncloc',
)
DASHBOARD_KPI_CONTEXT_KEYWORDS = (
    'dashboard',
    'kpi',
    'kpis',
    'overview',
    'pipeline kpis',
    'deployment kpis',
    'github',
    'github repo',
    'github page',
    'repo page',
    'finops',
    'finops page',
    'ecoops',
    'ecoops page',
    'sonarcloud',
    'sonarqube',
    'sonarcloud page',
)
DASHBOARD_KPI_EXPLANATION_KEYWORDS = (
    'what is',
    'what does',
    'mean',
    'means',
    'explain',
    'how is',
    'how does',
    'calculated',
    'calculation',
    'represent',
    'represents',
    'time window',
    'grouped by week',
    'grouped by month',
)
FINOPS_DASHBOARD_KPI_KEYWORDS = (
    'finops page',
    'daily total cost',
    'average / day',
    'highest day',
    'vs previous month',
    'daily cost chart',
    'finops total cost',
    'finops average',
    'finops highest day',
    'finops month change',
)
FINOPS_RAG_RESULT_LIMIT = 4
FINOPS_RAG_HISTORY_USER_MESSAGES = 3
FINOPS_RAG_STRONG_KEYWORDS = (
    'finops',
    'cost',
    'costs',
    'spend',
    'spending',
    'vm',
    'vms',
    'aks',
    'azure',
    'billing',
    'allocation',
    'allocations',
)
FINOPS_RAG_CONTEXT_KEYWORDS = (
    'pipeline',
    'pipelines',
    'build',
    'builds',
    'jenkins',
)
FINOPS_RAG_SECONDARY_KEYWORDS = (
    'expensive',
    'high',
    'higher',
    'spike',
    'why',
    'optimize',
    'optimization',
    'duration',
    'durations',
    'slow',
    'daily',
)
MONTH_NAME_TO_NUMBER = {
    'jan': 1,
    'january': 1,
    'feb': 2,
    'february': 2,
    'mar': 3,
    'march': 3,
    'apr': 4,
    'april': 4,
    'may': 5,
    'jun': 6,
    'june': 6,
    'jul': 7,
    'july': 7,
    'aug': 8,
    'august': 8,
    'sep': 9,
    'sept': 9,
    'september': 9,
    'oct': 10,
    'october': 10,
    'nov': 11,
    'november': 11,
    'dec': 12,
    'december': 12,
}
MONTH_PATTERN = (
    r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|'
    r'aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?'
)

def _extract_error_message(response):
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f'Ollama returned HTTP {response.status_code}.'

    if isinstance(payload, dict):
        error = (payload.get('error') or '').strip()
        if error:
            return error

    return f'Ollama returned HTTP {response.status_code}.'


def _coerce_year(raw_year):
    if raw_year is None:
        return date.today().year

    year = int(raw_year)
    if year < 100:
        year += 2000
    return year


def _safe_date(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def _extract_dates_from_text(text):
    content = str(text or '').strip().lower()
    if not content:
        return []

    matches = []
    seen = set()

    for item in re.finditer(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', content):
        parsed = _safe_date(item.group(1), item.group(2), item.group(3))
        if parsed and parsed.isoformat() not in seen:
            matches.append(parsed)
            seen.add(parsed.isoformat())

    for item in re.finditer(r'\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b', content):
        parsed = _safe_date(
            _coerce_year(item.group(3)),
            int(item.group(2)),
            int(item.group(1)),
        )
        if parsed and parsed.isoformat() not in seen:
            matches.append(parsed)
            seen.add(parsed.isoformat())

    for item in re.finditer(
        rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:/|-|\s)\s*({MONTH_PATTERN})(?:\s*(?:,|/|-|\s)\s*(\d{{2,4}}))?\b',
        content,
    ):
        month = MONTH_NAME_TO_NUMBER.get(item.group(2))
        parsed = _safe_date(_coerce_year(item.group(3)), month, int(item.group(1)))
        if parsed and parsed.isoformat() not in seen:
            matches.append(parsed)
            seen.add(parsed.isoformat())

    for item in re.finditer(
        rf'\b({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{2,4}}))?\b',
        content,
    ):
        month = MONTH_NAME_TO_NUMBER.get(item.group(1))
        parsed = _safe_date(_coerce_year(item.group(3)), month, int(item.group(2)))
        if parsed and parsed.isoformat() not in seen:
            matches.append(parsed)
            seen.add(parsed.isoformat())

    return matches


def _extract_month_scopes_from_text(text):
    content = str(text or '').strip().lower()
    if not content:
        return []

    matches = []
    seen = set()

    for item in re.finditer(r'\b(\d{4})-(\d{1,2})(?!-\d)\b', content):
        year = int(item.group(1))
        month = int(item.group(2))
        if not (1 <= month <= 12):
            continue
        key = (year, month)
        if key not in seen:
            matches.append(key)
            seen.add(key)

    for item in re.finditer(r'\b(\d{1,2})[/-](\d{4})\b', content):
        month = int(item.group(1))
        year = int(item.group(2))
        if not (1 <= month <= 12):
            continue
        key = (year, month)
        if key not in seen:
            matches.append(key)
            seen.add(key)

    for item in re.finditer(rf'\b({MONTH_PATTERN})\s+(\d{{4}})\b', content):
        month = MONTH_NAME_TO_NUMBER.get(item.group(1))
        year = int(item.group(2))
        if month is None:
            continue
        key = (year, month)
        if key not in seen:
            matches.append(key)
            seen.add(key)

    for item in re.finditer(
        rf'\b(?:in|for|during|throughout|across|within|through)\s+(?:the\s+month\s+of\s+)?({MONTH_PATTERN})(?:\s+(\d{{2,4}}))?\b',
        content,
    ):
        month = MONTH_NAME_TO_NUMBER.get(item.group(1))
        year = _coerce_year(item.group(2))
        if month is None:
            continue
        key = (year, month)
        if key not in seen:
            matches.append(key)
            seen.add(key)

    for item in re.finditer(rf'\bmonth\s+of\s+({MONTH_PATTERN})(?:\s+(\d{{2,4}}))?\b', content):
        month = MONTH_NAME_TO_NUMBER.get(item.group(1))
        year = _coerce_year(item.group(2))
        if month is None:
            continue
        key = (year, month)
        if key not in seen:
            matches.append(key)
            seen.add(key)

    return matches


def _recent_user_messages(messages, *, limit=FINOPS_RAG_HISTORY_USER_MESSAGES):
    items = []
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        if message.get('role') != 'user':
            continue
        content = message.get('content')
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        items.append(content)
        if len(items) >= limit:
            break
    return list(reversed(items))


def _build_retrieval_query(messages):
    return '\n'.join(_recent_user_messages(messages)).strip()


def _latest_user_message(messages):
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        if message.get('role') != 'user':
            continue
        content = message.get('content')
        if not isinstance(content, str):
            continue
        content = content.strip()
        if content:
            return content
    return ''


def _build_dashboard_kpi_system_message(rows):
    sections = []
    for index, row in enumerate(rows, start=1):
        if row is None:
            continue

        section_lines = [
            f'Document {index}',
            f"- key: {row.document_key or 'unknown'}",
            f"- page: {row.dashboard_page or 'unknown'}",
            f"- title: {row.title or 'unknown'}",
        ]
        section_lines.extend([
            '',
            str(row.content or '').strip(),
        ])
        sections.append('\n'.join(section_lines).strip())

    evidence_text = '\n\n'.join(section for section in sections if section)
    if not evidence_text:
        return None

    return {
        'role': 'system',
        'content': (
            'You are the Jenkins Monitor KPI assistant.\n'
            'Use the retrieved dashboard KPI documents below when answering questions about what a dashboard KPI means, how it is calculated, what time window it uses, or how a chart should be interpreted.\n'
            'These documents are generic KPI definitions, not live pipeline value snapshots.\n'
            'Prefer the stored KPI documentation over guesses.\n'
            'If the documentation says a metric comes directly from Jenkins, make that clear instead of pretending the dashboard recalculates it.\n'
            'Do not invent current KPI values from these documents.\n'
            'If the documentation does not prove a detail, say that clearly.\n'
            '\nRetrieved KPI documentation:\n'
            f'{evidence_text}'
        ).strip(),
    }


def _build_dashboard_kpi_rag_system_message(matches):
    sections = []
    for index, match in enumerate(matches, start=1):
        chunk_text = _clean_dashboard_kpi_chroma_text(match.get('document') or '')
        if not chunk_text:
            continue

        sections.append(
            '\n'.join(
                [
                    f'Evidence {index}',
                    chunk_text,
                ]
            )
        )

    evidence_text = '\n\n'.join(section for section in sections if section)
    if not evidence_text:
        return None

    return {
        'role': 'system',
        'content': (
            'You are the Jenkins Monitor KPI assistant.\n'
            'Answer only from the retrieved dashboard KPI evidence below.\n'
            'Reason from the evidence itself and answer naturally.\n'
            'If the evidence is insufficient, say so clearly.\n'
            '\nRetrieved KPI evidence:\n'
            f'{evidence_text}'
        ).strip(),
    }


def _build_missing_dashboard_kpi_evidence_message():
    return {
        'role': 'system',
        'content': (
            'You are the Jenkins Monitor KPI assistant.\n'
            'No relevant dashboard KPI evidence was retrieved from Chroma for this request.\n'
            'Do not answer with guessed KPI definitions, formulas, time windows, or UI behavior.\n'
            'Tell the user clearly that the KPI evidence could not be retrieved from the indexed knowledge base.'
        ).strip(),
    }


def _clean_dashboard_kpi_chroma_text(value):
    lines = []
    for raw_line in str(value or '').splitlines():
        line = raw_line.strip()
        if not line:
            lines.append('')
            continue

        lowered = line.lower()
        if lowered in ('dashboard kpi explanation', 'chunk content:'):
            continue
        if lowered.startswith('value mode:'):
            continue
        if lowered.startswith('dashboard page:'):
            continue
        if lowered.startswith('document key:'):
            continue
        if lowered.startswith('tags:'):
            continue
        if lowered.startswith('aliases:'):
            continue

        lines.append(line)

    cleaned = '\n'.join(lines).strip()
    return re.sub(r'\n{3,}', '\n\n', cleaned)


def _looks_like_dashboard_kpi_query(query_text):
    content = str(query_text or '').strip().lower()
    if not content:
        return False

    if any(keyword in content for keyword in DASHBOARD_KPI_METRIC_KEYWORDS):
        return True

    has_context = any(keyword in content for keyword in DASHBOARD_KPI_CONTEXT_KEYWORDS)
    has_explanation_intent = any(keyword in content for keyword in DASHBOARD_KPI_EXPLANATION_KEYWORDS)
    return has_context and has_explanation_intent


def _looks_like_finops_query(query_text):
    content = str(query_text or '').strip().lower()
    if not content:
        return False

    if (
        _looks_like_dashboard_kpi_query(content)
        and any(keyword in content for keyword in FINOPS_DASHBOARD_KPI_KEYWORDS)
    ):
        return False

    has_strong_keyword = any(keyword in content for keyword in FINOPS_RAG_STRONG_KEYWORDS)
    if has_strong_keyword:
        return True

    has_secondary_keyword = any(keyword in content for keyword in FINOPS_RAG_SECONDARY_KEYWORDS)
    has_context_keyword = any(keyword in content for keyword in FINOPS_RAG_CONTEXT_KEYWORDS)
    has_date_scope = bool(_extract_dates_from_text(content)) or bool(_extract_month_scopes_from_text(content))
    return has_context_keyword and has_secondary_keyword and has_date_scope


def _extract_target_usage_date(query_text):
    matches = _extract_dates_from_text(query_text)
    if len(matches) == 1:
        return matches[0]
    return None


def _extract_target_usage_month(query_text):
    matches = _extract_month_scopes_from_text(query_text)
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_target_usage_date(messages):
    latest_message = _latest_user_message(messages)
    latest_match = _extract_target_usage_date(latest_message)
    if latest_match is not None:
        return latest_match
    return _extract_target_usage_date(_build_retrieval_query(messages))


def _resolve_target_usage_month(messages, *, usage_date=None):
    if usage_date is not None:
        return None

    latest_message = _latest_user_message(messages)
    latest_match = _extract_target_usage_month(latest_message)
    if latest_match is not None:
        return latest_match
    return _extract_target_usage_month(_build_retrieval_query(messages))


def _format_duration_ms(duration_ms):
    total_seconds = max(int(duration_ms or 0) // 1000, 0)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f'{hours}h {minutes}m {seconds}s'
    if minutes:
        return f'{minutes}m {seconds}s'
    return f'{seconds}s'


def _format_number(value):
    try:
        return f'{float(value):.2f}'
    except (TypeError, ValueError):
        return '0.00'


def _format_cost(value, currency_code='USD'):
    try:
        return f'{currency_code or "USD"} {float(value):.4f}'
    except (TypeError, ValueError):
        return f'{currency_code or "USD"} 0.0000'


def _to_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(value):
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _format_ratio(value, baseline):
    baseline_value = _to_float(baseline)
    if baseline_value <= 0:
        return 'n/a'
    return f'{_to_float(value) / baseline_value:.2f}x'


def _render_json(value):
    try:
        return json.dumps(value or {}, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return '{}'


def _build_day_snapshot(row):
    summary = row.summary or {}
    cost_summary = summary.get('cost') or {}
    build_summary = summary.get('builds') or {}
    baseline_summary = summary.get('month_build_baseline') or {}
    signal_summary = summary.get('signals') or {}
    tags = summary.get('tags') or []

    return {
        'usage_date': row.usage_date.isoformat() if row.usage_date else 'unknown',
        'pipeline_name': row.pipeline_name or 'unknown',
        'pipeline_job_path': row.pipeline_job_path or '',
        'currency_code': cost_summary.get('currency_code') or row.currency_code or 'USD',
        'total_cost': _to_float(cost_summary.get('total_cost')),
        'month_average_total_cost': _to_float(cost_summary.get('month_average_total_cost')),
        'cost_ratio': _format_ratio(
            cost_summary.get('total_cost'),
            cost_summary.get('month_average_total_cost'),
        ),
        'month_rank': cost_summary.get('month_rank'),
        'month_rank_day_count': cost_summary.get('month_rank_day_count'),
        'build_count': _to_int(build_summary.get('build_count')),
        'success_count': _to_int(build_summary.get('success_count')),
        'failure_count': _to_int(build_summary.get('failure_count')),
        'aborted_count': _to_int(build_summary.get('aborted_count')),
        'running_count': _to_int(build_summary.get('running_count')),
        'total_duration_ms': _to_int(build_summary.get('total_duration_ms')),
        'avg_duration_ms': _to_int(build_summary.get('avg_duration_ms')),
        'avg_build_count_active_day': _to_float(baseline_summary.get('avg_build_count_active_day')),
        'avg_total_duration_ms_active_day': _to_int(
            baseline_summary.get('avg_total_duration_ms_active_day')
        ),
        'build_count_ratio': _format_ratio(
            build_summary.get('build_count'),
            baseline_summary.get('avg_build_count_active_day'),
        ),
        'duration_ratio': _format_ratio(
            build_summary.get('total_duration_ms'),
            baseline_summary.get('avg_total_duration_ms_active_day'),
        ),
        'cost_spike': bool(signal_summary.get('cost_spike')),
        'build_pressure': bool(signal_summary.get('build_pressure')),
        'high_build_activity': bool(signal_summary.get('high_build_activity')),
        'long_build_activity': bool(signal_summary.get('long_build_activity')),
        'failure_pressure': bool(signal_summary.get('failure_pressure')),
        'likely_driver': str(signal_summary.get('likely_driver') or 'unknown'),
        'tags': ', '.join(str(item) for item in tags) if tags else 'none',
        'structured_summary_json': _render_json(summary),
        'document_content': str(row.content or '').strip(),
    }


def _build_day_snapshot_lines(snapshot, *, heading=None):
    lines = []
    if heading:
        lines.extend([heading,])

    lines.extend([
        f"- usage_date: {snapshot['usage_date']}",
        f"- pipeline_name: {snapshot['pipeline_name']}",
        f"- pipeline_job_path: {snapshot['pipeline_job_path'] or 'unknown'}",
        f"- total_cost: {_format_cost(snapshot['total_cost'], snapshot['currency_code'])}",
        f"- month_average_total_cost: {_format_cost(snapshot['month_average_total_cost'], snapshot['currency_code'])}",
        f"- cost_vs_month_average_ratio: {snapshot['cost_ratio']}",
        f"- cost_spike: {snapshot['cost_spike']}",
        f"- month_cost_rank: {snapshot['month_rank'] if snapshot['month_rank'] is not None else 'unknown'}",
        f"- month_cost_rank_day_count: {snapshot['month_rank_day_count'] if snapshot['month_rank_day_count'] is not None else 'unknown'}",
        f"- build_count: {snapshot['build_count']}",
        f"- success_count: {snapshot['success_count']}",
        f"- failure_count: {snapshot['failure_count']}",
        f"- aborted_count: {snapshot['aborted_count']}",
        f"- running_count: {snapshot['running_count']}",
        f"- total_build_duration: {_format_duration_ms(snapshot['total_duration_ms'])}",
        f"- average_build_duration: {_format_duration_ms(snapshot['avg_duration_ms'])}",
        f"- month_average_builds_per_active_day: {snapshot['avg_build_count_active_day']}",
        f"- month_average_total_build_time_per_active_day: {_format_duration_ms(snapshot['avg_total_duration_ms_active_day'])}",
        f"- build_count_vs_active_day_average_ratio: {snapshot['build_count_ratio']}",
        f"- total_build_duration_vs_active_day_average_ratio: {snapshot['duration_ratio']}",
        f"- build_pressure: {snapshot['build_pressure']}",
        f"- high_build_activity: {snapshot['high_build_activity']}",
        f"- long_build_activity: {snapshot['long_build_activity']}",
        f"- failure_pressure: {snapshot['failure_pressure']}",
        f"- likely_driver: {snapshot['likely_driver']}",
        f"- tags: {snapshot['tags']}",
    ])

    if snapshot['structured_summary_json']:
        lines.extend([
            '',
            'Structured daily summary JSON:',
            snapshot['structured_summary_json'],
        ])

    if snapshot['document_content']:
        lines.extend([
            '',
            'Stored daily document text:',
            snapshot['document_content'],
        ])

    return lines


def _build_month_snapshot_line(row):
    snapshot = _build_day_snapshot(row)
    return (
        f"- {snapshot['usage_date']} | total_cost={_format_cost(snapshot['total_cost'], snapshot['currency_code'])} "
        f"| month_average_total_cost={_format_cost(snapshot['month_average_total_cost'], snapshot['currency_code'])} "
        f"| cost_vs_month_average_ratio={snapshot['cost_ratio']} | build_count={snapshot['build_count']} "
        f"| success_count={snapshot['success_count']} | failure_count={snapshot['failure_count']} "
        f"| aborted_count={snapshot['aborted_count']} | total_build_duration={_format_duration_ms(snapshot['total_duration_ms'])} "
        f"| average_build_duration={_format_duration_ms(snapshot['avg_duration_ms'])} "
        f"| build_count_vs_active_day_average_ratio={snapshot['build_count_ratio']} "
        f"| total_build_duration_vs_active_day_average_ratio={snapshot['duration_ratio']} "
        f"| cost_spike={snapshot['cost_spike']} | build_pressure={snapshot['build_pressure']} "
        f"| high_build_activity={snapshot['high_build_activity']} | long_build_activity={snapshot['long_build_activity']} "
        f"| failure_pressure={snapshot['failure_pressure']} | likely_driver={snapshot['likely_driver']}"
    )


def _build_finops_document_system_message(row):
    snapshot = _build_day_snapshot(row)
    daily_evidence_text = '\n'.join(_build_day_snapshot_lines(snapshot))

    return {
        'role': 'system',
        'content': (
            'You are the Jenkins Monitor FinOps assistant.\n'
            'The user asked about a specific stored daily FinOps document.\n'
            'Answer only from the retrieved evidence below.\n'
            'Reason from the stored cost values, build activity, monthly baseline, structured signals, and the stored daily document text.\n'
            'Treat any cause or driver as a likely explanation unless the evidence proves it directly.\n'
            'Do not invent dates, costs, build counts, failures, or Azure causes that are not supported by the evidence.\n'
            '\nRetrieved daily evidence:\n'
            f'{daily_evidence_text}'
        ).strip(),
    }


def _build_finops_rag_system_message(matches, *, usage_date=None):
    sections = []
    for index, match in enumerate(matches, start=1):
        metadata = match.get('metadata') or {}
        chunk_text = str(match.get('document') or '').strip()
        if not chunk_text:
            continue

        sections.append(
            '\n'.join(
                [
                    f'Evidence {index}',
                    f"- usage_date: {metadata.get('usage_date') or 'unknown'}",
                    f"- likely_driver: {metadata.get('likely_driver') or 'unknown'}",
                    f"- build_count: {metadata.get('build_count') or 0}",
                    f"- total_duration: {_format_duration_ms(metadata.get('total_duration_ms') or 0)}",
                    f"- total_cost: {metadata.get('total_cost') or 0}",
                    f"- tags: {metadata.get('tag_csv') or 'none'}",
                    chunk_text,
                ]
            )
        )

    target_date_line = ''
    if usage_date is not None:
        target_date_line = f'Target date inferred from the user request: {usage_date.isoformat()}\n'
    evidence_text = '\n\n'.join(sections)

    return {
        'role': 'system',
        'content': (
            'You are the Jenkins Monitor FinOps assistant.\n'
            'Use the retrieved FinOps evidence below when answering cost, VM, AKS, build, or pipeline questions.\n'
            'Ground every factual claim in the retrieved evidence.\n'
            'Reason from the dates, costs, build counts, build durations, stored signals, and chunk text itself.\n'
            'Treat stored signals such as likely_driver, cost_spike, and build_pressure as hints from the stored analysis, not absolute proof.\n'
            'If the evidence is incomplete, spans multiple dates, or cannot prove a cause, say that clearly.\n'
            'Do not invent numbers, dates, costs, failures, or Azure causes.\n'
            f'{target_date_line}'
            '\nRetrieved FinOps evidence:\n'
            f'{evidence_text}'
        ).strip(),
    }


def _month_bounds(year, month):
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _build_finops_month_system_message(rows, *, year, month):
    ordered_rows = sorted(
        [row for row in (rows or []) if row.usage_date is not None],
        key=lambda item: item.usage_date,
    )
    if not ordered_rows:
        return None

    total_days_in_month = monthrange(year, month)[1]
    covered_days = len(ordered_rows)
    missing_day_count = max(total_days_in_month - covered_days, 0)
    covered_dates = [row.usage_date.isoformat() for row in ordered_rows]

    total_cost = 0.0
    cost_day_count = 0
    total_build_count = 0
    success_count = 0
    failure_count = 0
    aborted_count = 0
    running_count = 0
    total_duration_ms = 0
    build_days = 0
    no_build_days = 0
    build_pressure_days = 0
    cost_spike_days = 0
    highest_build_count = 0
    highest_build_date = None

    for row in ordered_rows:
        summary = row.summary or {}
        cost_summary = summary.get('cost') or {}
        build_summary = summary.get('builds') or {}
        signal_summary = summary.get('signals') or {}

        build_count = int(build_summary.get('build_count') or 0)
        total_build_count += build_count
        success_count += int(build_summary.get('success_count') or 0)
        failure_count += int(build_summary.get('failure_count') or 0)
        aborted_count += int(build_summary.get('aborted_count') or 0)
        running_count += int(build_summary.get('running_count') or 0)
        total_duration_ms += int(build_summary.get('total_duration_ms') or 0)

        if build_count > 0:
            build_days += 1
        else:
            no_build_days += 1

        if build_count > highest_build_count:
            highest_build_count = build_count
            highest_build_date = row.usage_date.isoformat()

        if signal_summary.get('build_pressure'):
            build_pressure_days += 1
        if signal_summary.get('cost_spike'):
            cost_spike_days += 1

        if cost_summary.get('available'):
            total_cost += float(cost_summary.get('total_cost') or 0.0)
            cost_day_count += 1

    coverage_status = 'full_month' if covered_days == total_days_in_month else 'partial_month'
    average_daily_cost = (total_cost / cost_day_count) if cost_day_count > 0 else 0.0
    average_builds_per_covered_day = (
        total_build_count / covered_days
        if covered_days > 0 else 0.0
    )
    daily_evidence_lines = '\n'.join(_build_month_snapshot_line(row) for row in ordered_rows)

    return {
        'role': 'system',
        'content': (
            'You are the Jenkins Monitor FinOps assistant.\n'
            'The user asked about a month-wide period, not a single day.\n'
            'Answer from the stored monthly evidence below.\n'
            'Reason directly from the stored facts.\n'
            'Use the daily ledger to compare dates, costs, build activity, and ratios.\n'
            'If the user asks which day was highest, infer that from the daily total_cost values below.\n'
            'If the user asks why a day was costly, compare that day\'s cost and build activity against the monthly baseline and treat any cause as a likely explanation unless the evidence proves it directly.\n'
            'Do not generalize beyond the covered stored dates, and say clearly when coverage is partial.\n'
            '\nRetrieved monthly stored evidence:\n'
            f'- target_month: {year:04d}-{month:02d}\n'
            f'- coverage_status: {coverage_status}\n'
            f'- stored_day_count: {covered_days} of {total_days_in_month}\n'
            f'- covered_start_date: {covered_dates[0]}\n'
            f'- covered_end_date: {covered_dates[-1]}\n'
            f'- covered_dates: {", ".join(covered_dates)}\n'
            f'- missing_day_count: {missing_day_count}\n'
            f'- cost_day_count: {cost_day_count}\n'
            f'- total_cost_across_covered_days: {_format_cost(total_cost)}\n'
            f'- average_daily_cost_across_covered_days: {_format_cost(average_daily_cost)}\n'
            f'- build_days: {build_days}\n'
            f'- no_build_days: {no_build_days}\n'
            f'- total_build_count: {total_build_count}\n'
            f'- average_builds_per_covered_day: {average_builds_per_covered_day:.2f}\n'
            f'- success_count: {success_count}\n'
            f'- failure_count: {failure_count}\n'
            f'- aborted_count: {aborted_count}\n'
            f'- running_count: {running_count}\n'
            f'- total_build_duration: {_format_duration_ms(total_duration_ms)}\n'
            f'- build_pressure_days: {build_pressure_days}\n'
            f'- cost_spike_days: {cost_spike_days}\n'
            f'- busiest_build_day: {highest_build_date or "unknown"}\n'
            f'- busiest_build_day_count: {highest_build_count}\n'
            '\n'
            'Daily evidence ledger:\n'
            f'{daily_evidence_lines}\n'
        ).strip(),
    }


def _maybe_add_dashboard_kpi_context(messages):
    query_text = _build_retrieval_query(messages)
    if not _looks_like_dashboard_kpi_query(query_text):
        return messages

    try:
        matches = query_dashboard_kpi_chroma(
            query_text,
            limit=DASHBOARD_KPI_RAG_RESULT_LIMIT,
        )
    except Exception:
        current_app.logger.exception('Dashboard KPI Chroma retrieval failed.')
        matches = []

    if matches:
        system_message = _build_dashboard_kpi_rag_system_message(matches)
        if system_message is not None:
            return [system_message, *messages]

    return [_build_missing_dashboard_kpi_evidence_message(), *messages]


def _maybe_add_finops_rag_context(messages):
    query_text = _build_retrieval_query(messages)
    if not _looks_like_finops_query(query_text):
        return messages

    usage_date = _resolve_target_usage_date(messages)
    usage_month = _resolve_target_usage_month(messages, usage_date=usage_date)

    if usage_date is not None:
        try:
            row = get_finops_build_document(usage_date)
        except Exception:
            current_app.logger.exception('FinOps document retrieval failed.')
            row = None
        if row is not None:
            return [_build_finops_document_system_message(row), *messages]

    if usage_month is not None:
        start_date, end_date = _month_bounds(*usage_month)
        try:
            month_rows = list_finops_build_documents_for_range(start_date, end_date)
        except Exception:
            current_app.logger.exception('FinOps month document retrieval failed.')
            month_rows = []
        if month_rows:
            month_message = _build_finops_month_system_message(
                month_rows,
                year=usage_month[0],
                month=usage_month[1],
            )
            if month_message is not None:
                return [month_message, *messages]

    try:
        matches = query_finops_chroma(
            query_text,
            limit=FINOPS_RAG_RESULT_LIMIT,
            usage_date=usage_date,
        )
        if not matches and usage_date is not None:
            matches = query_finops_chroma(
                query_text,
                limit=FINOPS_RAG_RESULT_LIMIT,
                usage_date=None,
            )
    except Exception:
        current_app.logger.exception('FinOps Chroma retrieval failed.')
        matches = []

    if matches:
        rag_message = _build_finops_rag_system_message(matches, usage_date=usage_date)
        return [rag_message, *messages]

    return messages


def _get_chatbot_config():
    base_url = current_app.config.get('OLLAMA_BASE_URL')
    chat_endpoint = current_app.config.get('OLLAMA_CHAT_ENDPOINT')
    model = current_app.config.get('OLLAMA_MODEL')
    timeout = current_app.config.get('OLLAMA_TIMEOUT')

    if not base_url or not chat_endpoint or not model or timeout is None:
        raise RuntimeError('Chatbot configuration is missing from the application config.')

    return base_url.rstrip('/'), chat_endpoint, model, int(timeout)


def _raise_connection_error(exc, url, model):
    exc_name = type(exc).__name__
    detail = str(exc).strip() or 'No additional error details were provided.'
    raise RuntimeError(
        f'Could not reach Ollama at {url} for model "{model}". '
        f'{exc_name}: {detail} '
        'If Flask is running in Docker or WSL, 127.0.0.1 may point to the app container instead of the Ollama host.'
    ) from exc


def get_ollama_status():
    base_url, chat_endpoint, model, timeout = _get_chatbot_config()
    tags_url = f'{base_url}/api/tags'

    try:
        response = requests.get(tags_url, timeout=timeout)
    except requests.RequestException as exc:
        _raise_connection_error(exc, tags_url, model)

    if not response.ok:
        error_message = _extract_error_message(response)
        raise RuntimeError(f'Ollama health check failed: {error_message}')

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError('Ollama health check returned an invalid JSON response.') from exc

    models = []
    for item in payload.get('models', []) or []:
        name = item.get('name')
        if isinstance(name, str) and name:
            models.append(name)

    return {
        'ok': True,
        'base_url': base_url,
        'chat_endpoint': chat_endpoint,
        'model': model,
        'available_models': models,
    }


def chat_with_ollama(messages):
    if not isinstance(messages, list) or not messages:
        raise ValueError('Please send a message before using the chatbot.')

    augmented_messages = _maybe_add_dashboard_kpi_context(messages)
    augmented_messages = _maybe_add_finops_rag_context(augmented_messages)
    base_url, chat_endpoint, model, timeout = _get_chatbot_config()
    chat_url = f'{base_url}{chat_endpoint}'

    try:
        response = requests.post(
            chat_url,
            json={
                'model': model,
                'messages': augmented_messages,
                'stream': False,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        _raise_connection_error(exc, chat_url, model)

    if not response.ok:
        error_message = _extract_error_message(response)
        raise RuntimeError(f'Ollama request failed: {error_message}')

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError('Ollama returned an invalid JSON response.') from exc

    reply = (payload.get('message') or {}).get('content')
    if not isinstance(reply, str) or reply == '':
        raise RuntimeError('Ollama returned an empty response.')

    return {
        'reply': reply,
        'model': model,
    }
