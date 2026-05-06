"""Legacy compatibility wrappers for DB-backed auth helpers."""

from services.user_account_service import find_user, get_pending_count

__all__ = ['find_user', 'get_pending_count']
