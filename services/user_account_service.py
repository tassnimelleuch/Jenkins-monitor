from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from auth_models import UserAccount
from extensions import db


ROLE_ALIASES = {
    'admin': 'admin',
    'dev': 'developer',
    'developer': 'developer',
    'qa': 'tester',
    'tester': 'tester',
}
REGISTRABLE_ROLES = ('developer', 'tester')
USER_STATUSES = ('pending', 'approved', 'rejected')


def _utcnow():
    return datetime.now(timezone.utc)


def normalize_role(role):
    raw_role = (role or '').strip().lower()
    return ROLE_ALIASES.get(raw_role, raw_role)


def role_matches(role, allowed_roles):
    normalized_role = normalize_role(role)
    normalized_allowed = {normalize_role(item) for item in allowed_roles}
    return normalized_role in normalized_allowed


def find_user(username):
    value = (username or '').strip()
    if not value:
        return None

    return (
        UserAccount.query
        .filter(func.lower(UserAccount.username) == value.lower())
        .one_or_none()
    )


def get_pending_count():
    return UserAccount.query.filter_by(status='pending').count()


def get_user_groups():
    rows = (
        UserAccount.query
        .order_by(UserAccount.created_at.desc(), UserAccount.username.asc())
        .all()
    )
    return {
        'pending': [row for row in rows if row.status == 'pending'],
        'approved': [
            row for row in rows
            if row.status == 'approved' and normalize_role(row.role) != 'admin'
        ],
        'rejected': [row for row in rows if row.status == 'rejected'],
    }


def register_user(username, password, role):
    clean_username = (username or '').strip()
    clean_password = (password or '').strip()
    normalized_role = normalize_role(role)

    if not clean_username or not clean_password:
        raise ValueError('All fields are required.')
    if normalized_role not in REGISTRABLE_ROLES:
        raise ValueError('Please select a valid role.')
    if find_user(clean_username):
        raise ValueError(f'Username "{clean_username}" is already taken.')

    user = UserAccount(
        username=clean_username,
        password_hash=generate_password_hash(clean_password),
        role=normalized_role,
        status='pending',
        is_logged_in=False,
    )
    db.session.add(user)
    db.session.commit()
    return user


def authenticate_user(username, password):
    user = find_user(username)
    if not user or not check_password_hash(user.password_hash, password or ''):
        return None, 'Invalid username or password.'

    if user.status == 'pending':
        return None, 'Your account is awaiting admin approval.'

    if user.status == 'rejected':
        return None, 'Your registration was rejected.'

    user.is_logged_in = True
    user.last_login_at = _utcnow()
    db.session.commit()
    return user, None


def logout_user(username):
    user = find_user(username)
    if not user:
        return

    user.is_logged_in = False
    user.last_logout_at = _utcnow()
    db.session.commit()


def set_user_status(username, status):
    normalized_status = (status or '').strip().lower()
    if normalized_status not in USER_STATUSES:
        raise ValueError(f'Unsupported user status "{status}".')

    user = find_user(username)
    if not user:
        return None

    user.status = normalized_status
    if normalized_status == 'approved':
        user.approved_at = _utcnow()
        user.rejected_at = None
    elif normalized_status == 'rejected':
        user.rejected_at = _utcnow()
        user.is_logged_in = False
        user.last_logout_at = _utcnow()

    db.session.commit()
    return user


def get_active_session_user(username):
    user = find_user(username)
    if user is None:
        return None
    if user.status != 'approved':
        return None
    if not user.is_logged_in:
        return None
    return user


def ensure_admin_account():
    admin_username = (current_app.config.get('ADMIN_USERNAME') or 'admin').strip() or 'admin'
    admin_password = current_app.config.get('ADMIN_PASSWORD') or 'admin'

    user = find_user(admin_username)
    if user is None:
        user = UserAccount(
            username=admin_username,
            password_hash=generate_password_hash(admin_password),
            role='admin',
            status='approved',
            approved_at=_utcnow(),
            is_logged_in=False,
        )
        db.session.add(user)
        db.session.commit()
        return user

    changed = False
    if normalize_role(user.role) != 'admin':
        user.role = 'admin'
        changed = True
    if user.status != 'approved':
        user.status = 'approved'
        user.approved_at = user.approved_at or _utcnow()
        user.rejected_at = None
        changed = True

    if changed:
        db.session.commit()

    return user
