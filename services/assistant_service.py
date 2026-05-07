from __future__ import annotations

import requests
from flask import current_app


DEFAULT_OLLAMA_URL = 'http://127.0.0.1:11434'
DEFAULT_OLLAMA_MODEL = 'qwen'
DEFAULT_SYSTEM_PROMPT = (
    'You are a helpful AI assistant inside Jenkins Monitor. '
    'Answer clearly and concisely.'
)


def _normalize_messages(messages):
    normalized = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue

        role = (item.get('role') or '').strip().lower()
        content = (item.get('content') or '').strip()
        if role not in {'system', 'user', 'assistant'} or not content:
            continue

        normalized.append({'role': role, 'content': content})

    return normalized


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


def chat_with_ollama(messages):
    normalized_messages = _normalize_messages(messages)
    if not normalized_messages:
        raise ValueError('Please send a message before asking the assistant.')

    if normalized_messages[0]['role'] != 'system':
        normalized_messages.insert(0, {
            'role': 'system',
            'content': DEFAULT_SYSTEM_PROMPT,
        })

    base_url = (
        current_app.config.get('OLLAMA_BASE_URL')
        or DEFAULT_OLLAMA_URL
    ).rstrip('/')
    model = (current_app.config.get('OLLAMA_MODEL') or DEFAULT_OLLAMA_MODEL).strip()
    timeout = int(current_app.config.get('OLLAMA_TIMEOUT') or 60)

    try:
        response = requests.post(
            f'{base_url}/api/chat',
            json={
                'model': model,
                'messages': normalized_messages,
                'stream': False,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            'I could not reach Ollama. Make sure the Ollama server is running and the model is available.'
        ) from exc

    if not response.ok:
        error_message = _extract_error_message(response)
        raise RuntimeError(f'Ollama request failed: {error_message}')

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError('Ollama returned an invalid JSON response.') from exc

    reply = ((payload.get('message') or {}).get('content') or '').strip()
    if not reply:
        raise RuntimeError('Ollama returned an empty response.')

    return {
        'reply': reply,
        'model': model,
    }
