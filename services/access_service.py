from functools import wraps
from flask import redirect, session, url_for

from services.user_account_service import get_active_session_user, normalize_role, role_matches


ADMIN_ROLES = ('admin',)
BUILD_TRIGGER_ROLES = ('admin', 'developer', 'tester')
BUILD_ABORT_ROLES = ('admin', 'developer')
BUILD_MANAGER_ROLES = BUILD_ABORT_ROLES
DASHBOARD_ROLES = ('admin', 'developer', 'tester')
CHATBOT_ROLES = ADMIN_ROLES

ACCESS_RULES = {
    'start_builds': BUILD_TRIGGER_ROLES,
    'abort_builds': BUILD_ABORT_ROLES,
    'manage_builds': BUILD_MANAGER_ROLES,
    'export_pdf': ADMIN_ROLES,
    'alerts': ADMIN_ROLES,
    'check_alerts': ADMIN_ROLES,
    'chatbot': CHATBOT_ROLES,
    'github': BUILD_MANAGER_ROLES,
    'deployment': DASHBOARD_ROLES,
    'deployment_live_stream': BUILD_MANAGER_ROLES,
    'cluster_metrics': ADMIN_ROLES,
    'finops': ADMIN_ROLES,
    'ecoops': ADMIN_ROLES,
    'pdf_reports': ADMIN_ROLES,
    'manage_users': ADMIN_ROLES,
    'overview_build_charts': BUILD_MANAGER_ROLES,
    'overview_tests_chart': DASHBOARD_ROLES,
    'pipeline_management': BUILD_MANAGER_ROLES,
    'pipeline_test_analytics': DASHBOARD_ROLES,
    'vm_metrics': ADMIN_ROLES,
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


def _roles_for_access(access_key):
    roles = ACCESS_RULES.get(access_key)
    if roles is None:
        raise KeyError(f'Unknown access rule "{access_key}".')
    return roles


def user_has_access(role, access_key):
    return role_matches(role, _roles_for_access(access_key))


def build_access_context(role):
    return {
        access_key: user_has_access(role, access_key)
        for access_key in ACCESS_RULES
    }


def role_required(*roles):
    allowed_roles = tuple(normalize_role(role) for role in roles)

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            current_role = _current_session_role()
            if not current_role or not role_matches(current_role, allowed_roles):
                return redirect(url_for('auth.login'))
            return f(*args, **kwargs)
        return decorated
    return decorator

admin_required = role_required(*ADMIN_ROLES)
build_trigger_required = role_required(*BUILD_TRIGGER_ROLES)
build_abort_required = role_required(*BUILD_ABORT_ROLES)
build_manager_required = role_required(*BUILD_MANAGER_ROLES)
dashboard_user_required = role_required(*DASHBOARD_ROLES)
