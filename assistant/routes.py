from flask import jsonify, request

from assistant import assistant_bp
from services.access_service import role_required
from services.assistant_service import chat_with_ollama


@assistant_bp.route('/api/assistant/chat', methods=['POST'])
@role_required('admin', 'developer', 'tester')
def chat_api():
    payload = request.get_json(silent=True) or {}
    messages = payload.get('messages')
    message = (payload.get('message') or '').strip()

    if messages is None and message:
        messages = [{'role': 'user', 'content': message}]

    try:
        result = chat_with_ollama(messages)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({'error': str(exc)}), 502

    return jsonify(result)
