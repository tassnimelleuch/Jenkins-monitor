from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from collectors.jenkins_collector import _extract_test_report_summary
from services.jenkins_service import (
    _pipeline_head_has_changed,
    _snapshot_is_stale,
    _stored_payload_requires_refresh,
)
from services.pipeline_storage_service import (
    _apply_optional_build_quality_fields,
    _build_branch_summary,
    build_tests_duration_points,
)


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


def test_stored_payload_requires_refresh_when_same_head_build_is_missing_test_artifacts():
    stored_payload = {
        'pipeline': {'selected_branch': 'main'},
        'branches': {
            'main': {
                'last_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
                'last_completed_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
                'builds': [
                    {
                        'number': 48,
                        'result': 'SUCCESS',
                        'stages': [
                            {'name': 'pytest', 'duration_ms': 20_000},
                            {'name': 'pylint', 'duration_ms': 10_000},
                        ],
                    }
                ],
                'trends': {
                    'coverage': [{'number': 48, 'coverage': None, 'timestamp': 1000}],
                    'junit': [{'number': 48, 'total': None, 'passed': None, 'failed': None, 'skipped': None}],
                },
            }
        },
    }
    live_head = {
        'last_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
        'last_completed_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
    }

    assert _stored_payload_requires_refresh(stored_payload, live_head)


def test_stored_payload_does_not_require_refresh_when_same_head_build_has_test_artifacts():
    stored_payload = {
        'pipeline': {'selected_branch': 'main'},
        'branches': {
            'main': {
                'last_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
                'last_completed_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
                'builds': [
                    {
                        'number': 48,
                        'result': 'SUCCESS',
                        'stages': [
                            {'name': 'pytest', 'duration_ms': 20_000},
                            {'name': 'pylint', 'duration_ms': 10_000},
                        ],
                    }
                ],
                'trends': {
                    'coverage': [{'number': 48, 'coverage': 88.4, 'timestamp': 1000}],
                    'junit': [{'number': 48, 'total': 120, 'passed': 118, 'failed': 1, 'skipped': 1}],
                },
            }
        },
    }
    live_head = {
        'last_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
        'last_completed_build': {'number': 48, 'result': 'SUCCESS', 'timestamp': 1000},
    }

    assert not _stored_payload_requires_refresh(stored_payload, live_head)


def test_stored_payload_requires_refresh_when_old_build_is_missing_test_artifacts():
    stored_payload = {
        'pipeline': {'selected_branch': 'main'},
        'branches': {
            'main': {
                'last_build': {'number': 50, 'result': 'SUCCESS', 'timestamp': 2000},
                'last_completed_build': {'number': 50, 'result': 'SUCCESS', 'timestamp': 2000},
                'builds': [
                    {
                        'number': 50,
                        'result': 'SUCCESS',
                        'coverage_percent': 91.0,
                        'junit_total': 140,
                        'junit_passed': 139,
                        'junit_failed': 1,
                        'junit_skipped': 0,
                        'stages': [{'name': 'pytest', 'duration_ms': 20_000}],
                    },
                    {
                        'number': 43,
                        'result': 'SUCCESS',
                        'coverage_percent': None,
                        'junit_total': None,
                        'junit_passed': None,
                        'junit_failed': None,
                        'junit_skipped': None,
                        'stages': [{'name': 'pytest', 'duration_ms': 19_000}],
                    },
                ],
                'trends': {
                    'coverage': [{'number': 50, 'coverage': 91.0, 'timestamp': 2000}],
                    'junit': [{'number': 50, 'total': 140, 'passed': 139, 'failed': 1, 'skipped': 0}],
                },
            }
        },
    }
    live_head = {
        'last_build': {'number': 50, 'result': 'SUCCESS', 'timestamp': 2000},
        'last_completed_build': {'number': 50, 'result': 'SUCCESS', 'timestamp': 2000},
    }

    assert _stored_payload_requires_refresh(stored_payload, live_head)


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


def test_build_tests_duration_points_sum_unit_tests_pylint_and_sonarcloud():
    builds = [
        SimpleNamespace(
            build_number=51,
            result='SUCCESS',
            timestamp_ms=1_700_000_000_000,
            stages=[
                SimpleNamespace(stage_name='Unit Tests', duration_ms=60_000),
                SimpleNamespace(stage_name='pylint', duration_ms=15_000),
                SimpleNamespace(stage_name='SonarCloud Scan', duration_ms=45_000),
                SimpleNamespace(stage_name='Build Docker Image', duration_ms=30_000),
            ],
        )
    ]

    points = build_tests_duration_points(builds, branch_name='main', finished_only=True)

    assert points == [
        {
            'branch': 'main',
            'number': 51,
            'timestamp': 1_700_000_000_000,
            'result': 'SUCCESS',
            'total_duration_ms': 120_000,
            'unit_tests_ms': 60_000,
            'pylint_ms': 15_000,
            'sonarcloud_ms': 45_000,
            'matched_stage_count': 3,
        }
    ]


def test_build_tests_duration_points_return_empty_total_when_no_target_stages_exist():
    builds = [
        SimpleNamespace(
            build_number=52,
            result='SUCCESS',
            timestamp_ms=1_700_000_100_000,
            stages=[
                SimpleNamespace(stage_name='Checkout', duration_ms=5_000),
                SimpleNamespace(stage_name='Build Image', duration_ms=35_000),
            ],
        )
    ]

    points = build_tests_duration_points(builds, branch_name='main', include_empty=True)

    assert points == [
        {
            'branch': 'main',
            'number': 52,
            'timestamp': 1_700_000_100_000,
            'result': 'SUCCESS',
            'total_duration_ms': None,
            'unit_tests_ms': 0,
            'pylint_ms': 0,
            'sonarcloud_ms': 0,
            'matched_stage_count': 0,
        }
    ]


def test_build_tests_duration_points_skip_running_empty_and_non_unit_test_stages():
    builds = [
        SimpleNamespace(
            build_number=61,
            result='SUCCESS',
            timestamp_ms=1_700_000_200_000,
            stages=[
                SimpleNamespace(stage_name='pytest', duration_ms=20_000),
                SimpleNamespace(stage_name='pylint quality', duration_ms=10_000),
            ],
        ),
        SimpleNamespace(
            build_number=62,
            result=None,
            timestamp_ms=1_700_000_300_000,
            stages=[
                SimpleNamespace(stage_name='Unit Tests', duration_ms=25_000),
            ],
        ),
        SimpleNamespace(
            build_number=63,
            result='SUCCESS',
            timestamp_ms=1_700_000_400_000,
            stages=[
                SimpleNamespace(stage_name='Integration Tests', duration_ms=40_000),
                SimpleNamespace(stage_name='Deploy', duration_ms=40_000),
            ],
        ),
    ]

    points = build_tests_duration_points(builds, branch_name='main', finished_only=True)

    assert points == [
        {
            'branch': 'main',
            'number': 61,
            'timestamp': 1_700_000_200_000,
            'result': 'SUCCESS',
            'total_duration_ms': 30_000,
            'unit_tests_ms': 20_000,
            'pylint_ms': 10_000,
            'sonarcloud_ms': 0,
            'matched_stage_count': 2,
        }
    ]


def test_build_tests_duration_points_support_dict_builds_from_live_jenkins_path():
    builds = [
        {
            'number': 71,
            'result': 'SUCCESS',
            'timestamp': 1_700_000_500_000,
            'stages': [
                {'name': 'Tests', 'duration_ms': 12_000},
                {'name': 'Pylint Analysis', 'duration_ms': 8_000},
                {'name': 'Sonar Quality Gate', 'duration_ms': 5_000},
            ],
        }
    ]

    points = build_tests_duration_points(builds, branch_name='main', finished_only=True)

    assert points == [
        {
            'branch': 'main',
            'number': 71,
            'timestamp': 1_700_000_500_000,
            'result': 'SUCCESS',
            'total_duration_ms': 25_000,
            'unit_tests_ms': 12_000,
            'pylint_ms': 8_000,
            'sonarcloud_ms': 5_000,
            'matched_stage_count': 3,
        }
    ]


def test_apply_optional_build_quality_fields_preserves_existing_history_when_payload_is_missing():
    row = SimpleNamespace(
        coverage_percent=87.5,
        junit_total=120,
        junit_passed=118,
        junit_failed=1,
        junit_skipped=1,
    )
    payload = {
        'coverage_percent': None,
        'has_coverage_percent': False,
        'junit_total': None,
        'junit_passed': None,
        'junit_failed': None,
        'junit_skipped': None,
        'has_junit_report': False,
    }

    _apply_optional_build_quality_fields(row, payload)

    assert row.coverage_percent == 87.5
    assert row.junit_total == 120
    assert row.junit_passed == 118
    assert row.junit_failed == 1
    assert row.junit_skipped == 1


def test_apply_optional_build_quality_fields_overwrites_existing_history_when_payload_has_data():
    row = SimpleNamespace(
        coverage_percent=87.5,
        junit_total=120,
        junit_passed=118,
        junit_failed=1,
        junit_skipped=1,
    )
    payload = {
        'coverage_percent': 91.3,
        'has_coverage_percent': True,
        'junit_total': 140,
        'junit_passed': 139,
        'junit_failed': 1,
        'junit_skipped': 0,
        'has_junit_report': True,
    }

    _apply_optional_build_quality_fields(row, payload)

    assert row.coverage_percent == 91.3
    assert row.junit_total == 140
    assert row.junit_passed == 139
    assert row.junit_failed == 1
    assert row.junit_skipped == 0


def test_extract_test_report_summary_reads_nested_jenkins_api_payload():
    data = {
        '_class': 'hudson.tasks.junit.TestResultAction',
        'childReport': {
            'result': {
                'totalCount': 18,
                'failCount': 2,
                'skipCount': 1,
            }
        },
    }

    assert _extract_test_report_summary(data) == {
        'total': 18,
        'passed': 15,
        'failed': 2,
        'skipped': 1,
    }
