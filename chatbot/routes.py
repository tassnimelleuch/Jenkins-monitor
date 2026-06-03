from flask import current_app, jsonify, request, session

from chatbot import chatbot_bp
from services.access_service import user_has_access
from services.chatbot_service import chat_with_ollama
from services.user_account_service import get_active_session_user, normalize_role


@chatbot_bp.route('/api/chatbot/chat', methods=['POST'])
def chat_api():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Please sign in to use the AI assistant.'}), 401

    user = get_active_session_user(username)
    if user is None:
        session.clear()
        return jsonify({'error': 'Your session expired. Please sign in again.'}), 401

    current_role = normalize_role(user.role)
    session['role'] = current_role
    if not user_has_access(current_role, 'chatbot'):
        return jsonify({'error': 'Your account does not have access to the AI assistant.'}), 403

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
        current_app.logger.exception('Chatbot request failed: %s', exc)
        return jsonify({'error': 'The chatbot is unavailable right now.'}), 502

    return jsonify(result)
