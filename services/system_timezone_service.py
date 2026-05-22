from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def _detect_system_timezone_name():
    env_tz = (os.getenv('TZ') or '').strip()
    if env_tz:
        try:
            ZoneInfo(env_tz)
            return env_tz
        except Exception:  # noqa: BLE001
            pass

    localtime_path = Path('/etc/localtime')
    try:
        resolved = localtime_path.resolve(strict=True)
        marker = '/zoneinfo/'
        resolved_str = str(resolved)
        if marker in resolved_str:
            candidate = resolved_str.split(marker, 1)[1]
            ZoneInfo(candidate)
            return candidate
    except Exception:  # noqa: BLE001
        pass

    return 'UTC'


SYSTEM_TIME_ZONE_NAME = _detect_system_timezone_name()
SYSTEM_TIME_ZONE = ZoneInfo(SYSTEM_TIME_ZONE_NAME)


def get_system_timezone_name():
    return SYSTEM_TIME_ZONE_NAME


def get_system_timezone():
    return SYSTEM_TIME_ZONE


def now_system_timezone():
    return datetime.now(SYSTEM_TIME_ZONE)


def to_system_timezone(value, assume_timezone=timezone.utc):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=assume_timezone)
    return value.astimezone(SYSTEM_TIME_ZONE)


def format_system_datetime(value, fmt='%Y-%m-%d %H:%M:%S %Z'):
    converted = to_system_timezone(value)
    if converted is None:
        return None
    return converted.strftime(fmt)
