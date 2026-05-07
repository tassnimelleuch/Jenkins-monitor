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


def chat_with_ollama(messages):
    if not isinstance(messages, list) or not messages:
        raise ValueError('Please send a message before using the chatbot.')

    base_url, chat_endpoint, model, timeout = _get_chatbot_config()

    try:
        response = requests.post(
            f'{base_url}{chat_endpoint}',
            json={
                'model': model,
                'messages': messages,
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

    reply = (payload.get('message') or {}).get('content')
    if not isinstance(reply, str) or reply == '':
        raise RuntimeError('Ollama returned an empty response.')

    return {
        'reply': reply,
        'model': model,
    }
