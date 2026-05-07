from flask import jsonify, request

from chatbot import chatbot_bp
from services.access_service import role_required
from services.chatbot_service import chat_with_ollama, get_ollama_status


@chatbot_bp.route('/api/chatbot/chat', methods=['POST'])
@role_required('admin', 'developer', 'tester')
def chat_api():
    payload = request.get_json(silent=True) or {}
    messages = payload.get('messages')
    message = payload.get('message')

    if messages is None and isinstance(message, str) and message:
        messages = [{'role': 'user', 'content': message}]

    try:
        result = chat_with_ollama(messages)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({'error': str(exc)}), 502

    return jsonify(result)


@chatbot_bp.route('/api/chatbot/health', methods=['GET'])
@role_required('admin', 'developer', 'tester')
def chatbot_health_api():
    try:
        return jsonify(get_ollama_status())
    except RuntimeError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502
