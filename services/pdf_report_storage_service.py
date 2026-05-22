from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from flask import current_app
from services.system_timezone_service import format_system_datetime, now_system_timezone, to_system_timezone
from werkzeug.utils import secure_filename


def _reports_dir() -> Path:
    reports_dir = Path(current_app.config['PDF_REPORTS_DIR'])
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def _parse_iso_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_pdf_filename(value):
    cleaned = secure_filename(value or '')
    if not cleaned:
        cleaned = 'jenkins-monitor-report.pdf'
    if not cleaned.lower().endswith('.pdf'):
        cleaned = f'{cleaned}.pdf'
    return cleaned


def _unique_path(base_name: str) -> Path:
    reports_dir = _reports_dir()
    candidate = reports_dir / base_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix or '.pdf'
    counter = 2
    while True:
        next_candidate = reports_dir / f'{stem}-{counter}{suffix}'
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def _format_timestamp(value: datetime) -> str:
    return format_system_datetime(value) or '--'


def _report_item_from_path(path: Path):
    stats = path.stat()
    exported_at = to_system_timezone(datetime.fromtimestamp(stats.st_mtime, tz=timezone.utc))

    return {
        'file_name': path.name,
        'size_bytes': stats.st_size,
        'size_kb': round(stats.st_size / 1024, 1),
        'exported_at_iso': exported_at.isoformat(),
        'exported_at_label': _format_timestamp(exported_at),
        'absolute_path': str(path.resolve()),
    }


def list_pdf_reports():
    reports_dir = _reports_dir()
    items = []

    for path in reports_dir.glob('*.pdf'):
        if not path.is_file():
            continue
        items.append(_report_item_from_path(path))

    items.sort(key=lambda item: item['exported_at_iso'], reverse=True)
    return items


def store_pdf_report(file_storage, generated_at=None, preferred_file_name=None):
    if file_storage is None:
        raise ValueError('No PDF file was provided.')

    file_name = _safe_pdf_filename(preferred_file_name or file_storage.filename)
    target_path = _unique_path(file_name)
    file_storage.save(target_path)

    exported_at = _parse_iso_datetime(generated_at) or now_system_timezone()
    timestamp = exported_at.timestamp()
    os.utime(target_path, (timestamp, timestamp))

    return _report_item_from_path(target_path)


def get_pdf_report_path(file_name):
    safe_name = _safe_pdf_filename(file_name)
    path = _reports_dir() / safe_name
    if not path.exists() or not path.is_file():
        return None
    return path


def get_pdf_reports_dir():
    return str(_reports_dir().resolve())
