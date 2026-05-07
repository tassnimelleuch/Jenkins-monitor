from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import func, inspect, text
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
TIME_FORMATS = ('12h', '24h')
DATE_FORMATS = ('dd/mm/yyyy', 'mm/dd/yyyy', 'yyyy-mm-dd')
TIME_ZONE_CHOICES = (
    ('browser', 'Browser local time'),
    ('UTC', 'UTC'),
    ('Africa/Tunis', 'Africa/Tunis'),
    ('Europe/London', 'Europe/London'),
    ('Europe/Paris', 'Europe/Paris'),
    ('America/New_York', 'America/New_York'),
    ('America/Chicago', 'America/Chicago'),
    ('America/Los_Angeles', 'America/Los_Angeles'),
    ('Asia/Dubai', 'Asia/Dubai'),
)
DEFAULT_USER_PREFERENCES = {
    'time_format': '24h',
    'date_format': 'dd/mm/yyyy',
    'timezone': 'browser',
    'show_seconds': False,
}
_TIME_ZONE_VALUES = {value for value, _label in TIME_ZONE_CHOICES}
_USER_PREFERENCE_COLUMN_DDLS = {
    'time_format': (
        "ALTER TABLE user_accounts "
        "ADD COLUMN time_format VARCHAR(10) NOT NULL DEFAULT '24h'"
    ),
    'date_format': (
        "ALTER TABLE user_accounts "
        "ADD COLUMN date_format VARCHAR(20) NOT NULL DEFAULT 'dd/mm/yyyy'"
    ),
    'time_zone': (
        "ALTER TABLE user_accounts "
        "ADD COLUMN time_zone VARCHAR(64) NOT NULL DEFAULT 'browser'"
    ),
    'show_seconds': (
        "ALTER TABLE user_accounts "
        "ADD COLUMN show_seconds BOOLEAN NOT NULL DEFAULT FALSE"
    ),
}


def _utcnow():
    return datetime.now(timezone.utc)


def _normalized_preference_value(value, allowed, default):
    normalized = (value or '').strip()
    return normalized if normalized in allowed else default


def _preferences_from_user(user):
    if user is None:
        return DEFAULT_USER_PREFERENCES.copy()

    return {
        'time_format': _normalized_preference_value(
            getattr(user, 'time_format', None),
            TIME_FORMATS,
            DEFAULT_USER_PREFERENCES['time_format'],
        ),
        'date_format': _normalized_preference_value(
            getattr(user, 'date_format', None),
            DATE_FORMATS,
            DEFAULT_USER_PREFERENCES['date_format'],
        ),
        'timezone': _normalized_preference_value(
            getattr(user, 'time_zone', None),
            _TIME_ZONE_VALUES,
            DEFAULT_USER_PREFERENCES['timezone'],
        ),
        'show_seconds': bool(getattr(user, 'show_seconds', False)),
    }


def normalize_role(role):
    raw_role = (role or '').strip().lower()
    return ROLE_ALIASES.get(raw_role, raw_role)


def role_matches(role, allowed_roles):
    normalized_role = normalize_role(role)
    normalized_allowed = {normalize_role(item) for item in allowed_roles}
    return normalized_role in normalized_allowed


def ensure_user_preference_columns():
    inspector = inspect(db.engine)
    existing_columns = {
        column['name']
        for column in inspector.get_columns(UserAccount.__tablename__)
    }
    missing_columns = [
        name for name in _USER_PREFERENCE_COLUMN_DDLS
        if name not in existing_columns
    ]
    if not missing_columns:
        return

    for column_name in missing_columns:
        db.session.execute(text(_USER_PREFERENCE_COLUMN_DDLS[column_name]))
    db.session.commit()


def get_time_zone_choices():
    return TIME_ZONE_CHOICES


def get_user_preferences(user_or_username=None):
    if isinstance(user_or_username, UserAccount):
        return _preferences_from_user(user_or_username)
    if user_or_username is None:
        return DEFAULT_USER_PREFERENCES.copy()
    return _preferences_from_user(find_user(user_or_username))


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


def update_user_preferences(username, time_format, date_format, timezone_value, show_seconds):
    user = find_user(username)
    if not user:
        raise ValueError('User not found.')

    normalized_time_format = _normalized_preference_value(
        time_format,
        TIME_FORMATS,
        None,
    )
    normalized_date_format = _normalized_preference_value(
        date_format,
        DATE_FORMATS,
        None,
    )
    normalized_time_zone = _normalized_preference_value(
        timezone_value,
        _TIME_ZONE_VALUES,
        None,
    )

    if normalized_time_format is None:
        raise ValueError('Please choose a valid time format.')
    if normalized_date_format is None:
        raise ValueError('Please choose a valid date format.')
    if normalized_time_zone is None:
        raise ValueError('Please choose a valid timezone.')

    user.time_format = normalized_time_format
    user.date_format = normalized_date_format
    user.time_zone = normalized_time_zone
    user.show_seconds = bool(show_seconds)
    db.session.commit()
    return _preferences_from_user(user)


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
