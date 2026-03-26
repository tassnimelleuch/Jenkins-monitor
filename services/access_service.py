from functools import wraps
from flask import session, redirect, url_for


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') not in roles:
                return redirect(url_for('auth.login'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated