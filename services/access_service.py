from functools import wraps
from flask import redirect, session, url_for

from services.user_account_service import get_active_session_user, normalize_role, role_matches

CHART_ROLE_RULES = {
    'overview_latest_builds': ('admin', 'developer'),
    'overview_build_trend': ('admin', 'developer'),
    'overview_build_history': ('admin', 'developer'),
    'pipeline_duration': ('admin', 'developer'),
    'pipeline_stage_failure': ('admin', 'developer'),
    'pipeline_coverage': ('admin', 'developer', 'tester'),
    'pipeline_junit': ('admin', 'developer'),
    'vm_cpu': ('admin',),
    'vm_ram': ('admin',),
    'vm_network': ('admin',),
    'vm_disk': ('admin',),
}


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


def dashboard_user_required(f):
    return role_required('admin', 'developer', 'tester')(f)


def admin_tester_required(f):
    return role_required('admin', 'tester')(f)


def can_view_chart(role, chart_key):
    allowed_roles = CHART_ROLE_RULES.get(chart_key)
    if not allowed_roles:
        return True
    return role_matches(role, allowed_roles)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        current_role = _current_session_role()
        if current_role != 'admin':
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated
