from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.jenkins_service import _pipeline_head_has_changed, _snapshot_is_stale
from services.pipeline_storage_service import _build_branch_summary


def test_snapshot_is_stale_without_timestamp():
    assert _snapshot_is_stale(None)


def test_snapshot_is_stale_after_threshold():
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    last_synced_at = now - timedelta(seconds=31)

    assert _snapshot_is_stale(last_synced_at, max_age_seconds=30, now=now)


def test_snapshot_is_not_stale_before_threshold():
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    last_synced_at = now - timedelta(seconds=29)

    assert not _snapshot_is_stale(last_synced_at, max_age_seconds=30, now=now)


def test_pipeline_head_has_changed_when_last_completed_build_changes():
    stored_payload = {
        'pipeline': {'selected_branch': 'main'},
        'branches': {
            'main': {
                'last_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
                'last_completed_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
            }
        },
    }
    live_head = {
        'last_build': {'number': 49, 'result': None, 'timestamp': 2000},
        'last_completed_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
    }

    assert _pipeline_head_has_changed(stored_payload, live_head)


def test_pipeline_head_has_not_changed_when_build_refs_match():
    stored_payload = {
        'pipeline': {'selected_branch': 'main'},
        'branches': {
            'main': {
                'last_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
                'last_completed_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
            }
        },
    }
    live_head = {
        'last_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
        'last_completed_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
    }

    assert not _pipeline_head_has_changed(stored_payload, live_head)


def test_build_branch_summary_uses_stored_build_rows_for_counts():
    branch_row = SimpleNamespace(
        total_builds=37,
        successful_builds=30,
        failed_builds=5,
        aborted_builds=2,
        running_builds=0,
        avg_duration_ms=None,
        avg_duration_seconds=None,
        success_rate=None,
        last_build_number=None,
        last_completed_build_number=None,
        health_score=100,
    )
    build_rows = [
        SimpleNamespace(build_number=number, result='SUCCESS', duration_ms=1000)
        for number in range(48, 8, -1)
    ]

    summary = _build_branch_summary(branch_row, build_rows)

    assert summary['total_builds'] == 40
    assert summary['successful'] == 40
