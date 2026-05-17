from __future__ import annotations

import re
from datetime import date

import requests
from flask import current_app

from services.finops_build_documents_service import get_finops_build_document
from services.finops_chroma_service import query_finops_chroma


FINOPS_RAG_RESULT_LIMIT = 4
FINOPS_RAG_HISTORY_USER_MESSAGES = 3
FINOPS_RAG_PRIMARY_KEYWORDS = (
    'finops',
    'cost',
    'costs',
    'spend',
    'spending',
    'vm',
    'vms',
    'aks',
    'jenkins',
    'pipeline',
    'pipelines',
    'build',
    'builds',
    'allocation',
    'allocations',
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

    month_pattern = r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?'
    for item in re.finditer(
        rf'\b(\d{{1,2}})\s*(?:/|-|\s)\s*({month_pattern})(?:\s*(?:,|/|-|\s)\s*(\d{{2,4}}))?\b',
        content,
    ):
        month = MONTH_NAME_TO_NUMBER.get(item.group(2))
        parsed = _safe_date(_coerce_year(item.group(3)), month, int(item.group(1)))
        if parsed and parsed.isoformat() not in seen:
            matches.append(parsed)
            seen.add(parsed.isoformat())

    for item in re.finditer(
        rf'\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{2,4}}))?\b',
        content,
    ):
        month = MONTH_NAME_TO_NUMBER.get(item.group(1))
        parsed = _safe_date(_coerce_year(item.group(3)), month, int(item.group(2)))
        if parsed and parsed.isoformat() not in seen:
            matches.append(parsed)
            seen.add(parsed.isoformat())

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


def _looks_like_finops_query(query_text):
    content = str(query_text or '').strip().lower()
    if not content:
        return False

    has_primary_keyword = any(keyword in content for keyword in FINOPS_RAG_PRIMARY_KEYWORDS)
    if has_primary_keyword:
        return True

    has_secondary_keyword = any(keyword in content for keyword in FINOPS_RAG_SECONDARY_KEYWORDS)
    return bool(_extract_dates_from_text(content)) and has_secondary_keyword


def _extract_target_usage_date(query_text):
    matches = _extract_dates_from_text(query_text)
    if len(matches) == 1:
        return matches[0]
    return None


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


def _build_finops_document_system_message(row):
    summary = row.summary or {}
    cost_summary = summary.get('cost') or {}
    build_summary = summary.get('builds') or {}
    baseline_summary = summary.get('month_build_baseline') or {}
    signal_summary = summary.get('signals') or {}
    tags = summary.get('tags') or []
    build_activity_label = 'no Jenkins builds were stored for this date'
    if (build_summary.get('build_count') or 0) > 0:
        if signal_summary.get('build_pressure'):
            build_activity_label = 'Jenkins build activity was above the normal active-day pattern'
        else:
            build_activity_label = 'Jenkins build activity stayed near the normal active-day pattern'

    return {
        'role': 'system',
        'content': (
            'You are the Jenkins Monitor FinOps assistant.\n'
            'The user asked about a specific stored daily FinOps document.\n'
            'Answer only from the retrieved document and metadata below.\n'
            'Do not add causes that are not explicitly supported by the document.\n'
            'If the document says build activity was near normal, do not describe it as high or unusually heavy.\n'
            'Do not claim retries, repeated builds, unnecessary resources, or failures unless they are explicitly stated in the document.\n'
            'Use this response format exactly:\n'
            'Facts:\n'
            '- Cost evidence: ...\n'
            '- Jenkins evidence: ...\n'
            'Conclusion:\n'
            '- ...\n'
            'Limits:\n'
            '- ...\n'
            'Under Facts, you must include at least one Cost evidence bullet and at least one Jenkins evidence bullet.\n'
            'If Jenkins activity stayed near normal, say that explicitly in the Jenkins evidence bullet.\n'
            '\nRetrieved daily document metadata:\n'
            f"- usage_date: {row.usage_date.isoformat() if row.usage_date else 'unknown'}\n"
            f"- pipeline_name: {row.pipeline_name or 'unknown'}\n"
            f"- total_cost: {_format_number(cost_summary.get('total_cost'))} {cost_summary.get('currency_code') or row.currency_code or 'USD'}\n"
            f"- vm_cost: {_format_number(cost_summary.get('vm_cost'))}\n"
            f"- aks_cost: {_format_number(cost_summary.get('aks_cost'))}\n"
            f"- other_cost: {_format_number(cost_summary.get('other_cost'))}\n"
            f"- month_average_total_cost: {_format_number(cost_summary.get('month_average_total_cost'))}\n"
            f"- build_count: {build_summary.get('build_count') or 0}\n"
            f"- success_count: {build_summary.get('success_count') or 0}\n"
            f"- failure_count: {build_summary.get('failure_count') or 0}\n"
            f"- aborted_count: {build_summary.get('aborted_count') or 0}\n"
            f"- total_duration: {_format_duration_ms(build_summary.get('total_duration_ms') or 0)}\n"
            f"- average_duration: {_format_duration_ms(build_summary.get('avg_duration_ms') or 0)}\n"
            f"- month_average_builds_per_active_day: {baseline_summary.get('avg_build_count_active_day') or 0}\n"
            f"- month_average_total_build_time_per_active_day: {_format_duration_ms(baseline_summary.get('avg_total_duration_ms_active_day') or 0)}\n"
            f"- build_activity_assessment: {build_activity_label}\n"
            f"- likely_driver: {signal_summary.get('likely_driver') or 'unknown'}\n"
            f"- build_pressure: {bool(signal_summary.get('build_pressure'))}\n"
            f"- high_build_activity: {bool(signal_summary.get('high_build_activity'))}\n"
            f"- long_build_activity: {bool(signal_summary.get('long_build_activity'))}\n"
            f"- vm_cost_dominant: {bool(signal_summary.get('vm_cost_dominant'))}\n"
            f"- aks_cost_dominant: {bool(signal_summary.get('aks_cost_dominant'))}\n"
            f"- failure_pressure: {bool(signal_summary.get('failure_pressure'))}\n"
            f"- tags: {', '.join(str(item) for item in tags) if tags else 'none'}\n"
            '\nRetrieved daily document content:\n'
            f'{row.content or ""}'
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
            'Use the retrieved dashboard evidence below when answering cost, VM, AKS, build, or pipeline questions.\n'
            'Ground factual claims in the evidence. If the evidence is incomplete, say so clearly.\n'
            'For "why was this day high/expensive" questions, treat explanations as likely contributors based on correlation, not absolute billing proof.\n'
            'Do not claim failures, retries, unused resources, or other causes unless the retrieved evidence explicitly supports them.\n'
            'Do not invent numbers, dates, or causes that are not supported by the retrieved evidence.\n'
            'Use this response format exactly:\n'
            'Facts:\n'
            '- Cost evidence: ...\n'
            '- Jenkins evidence: ...\n'
            'Conclusion:\n'
            '- ...\n'
            'Limits:\n'
            '- ...\n'
            'Under Facts, include both cost evidence and Jenkins evidence when the retrieved context contains both.\n'
            f'{target_date_line}'
            '\nRetrieved FinOps evidence:\n'
            f'{evidence_text}'
        ).strip(),
    }


def _maybe_add_finops_rag_context(messages):
    query_text = _build_retrieval_query(messages)
    if not _looks_like_finops_query(query_text):
        return messages

    usage_date = _extract_target_usage_date(query_text)
    if usage_date is not None:
        try:
            row = get_finops_build_document(usage_date)
        except Exception:
            current_app.logger.exception('FinOps document retrieval failed.')
            row = None
        if row is not None:
            return [_build_finops_document_system_message(row), *messages]

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
        return messages

    if not matches:
        return messages

    rag_message = _build_finops_rag_system_message(matches, usage_date=usage_date)
    return [rag_message, *messages]


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

    augmented_messages = _maybe_add_finops_rag_context(messages)
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
