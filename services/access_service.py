from functools import wraps
from flask import redirect, session, url_for

from services.user_account_service import get_active_session_user, normalize_role, role_matches


def _current_session_role():
    username = session.get('username')
    if not username:
        return None

    user = get_active_session_user(username)
    if user is None:
        session.clear()
        return None

    session['role'] = normalize_role(user.role)
    return session['role']


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            current_role = _current_session_role()
            if not current_role or not role_matches(current_role, roles):
                return redirect(url_for('auth.login'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        current_role = _current_session_role()
        if current_role != 'admin':
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated
