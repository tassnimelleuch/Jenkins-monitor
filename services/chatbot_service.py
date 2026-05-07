from __future__ import annotations

import requests
from flask import current_app


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

    base_url, chat_endpoint, model, timeout = _get_chatbot_config()
    chat_url = f'{base_url}{chat_endpoint}'

    try:
        response = requests.post(
            chat_url,
            json={
                'model': model,
                'messages': messages,
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
