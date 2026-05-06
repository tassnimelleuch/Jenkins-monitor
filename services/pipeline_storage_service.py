from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from pipeline_storage_models import (
    PipelineBranch,
    PipelineBranchBuild,
    PipelineBranchBuildStage,
    PipelineBranchStageKpi,
    PipelineBuildDuration,
    PipelineDefinition,
    PipelineStageDuration,
)


def _utcnow():
    return datetime.now(timezone.utc)


def _millis_to_datetime(value):
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _datetime_to_millis(value):
    if not value:
        return 0
    return int(value.timestamp() * 1000)


def _normalize_job_path(job_path):
    raw_job = (job_path or '').strip().strip('/')
    if not raw_job:
        return ''

    normalized = raw_job.replace('/job/', '/')
    if normalized.startswith('job/'):
        normalized = normalized[4:]

    return '/'.join(part for part in normalized.split('/') if part)


def _selected_branch_from_config():
    branch = (current_app.config.get('JENKINS_BRANCH') or 'main').strip().strip('/')
    return branch or 'main'


def _pipeline_job_path_from_config():
    normalized = _normalize_job_path(current_app.config.get('JENKINS_JOB'))
    if not normalized:
        return ''

    parts = [part for part in normalized.split('/') if part]
    branch = _selected_branch_from_config()
    if branch and len(parts) > 1 and parts[-1] == branch:
        parts = parts[:-1]
    return '/'.join(parts)


def _pipeline_name_from_job_path(job_path):
    if not job_path:
        return 'Jenkins Pipeline'
    return job_path.split('/')[-1]


def _load_pipeline_definition():
    job_path = _pipeline_job_path_from_config()
    query = PipelineDefinition.query.filter_by(source_system='jenkins')
    if job_path:
        pipeline = query.filter_by(job_path=job_path).one_or_none()
        if pipeline is not None:
            return pipeline
    return query.order_by(PipelineDefinition.last_synced_at.desc()).first()


def _serialize_branch_build(build, branch_name):
    if build is None:
        return None

    return {
        'branch': branch_name,
        'number': build.build_number,
        'result': build.result,
        'timestamp': build.timestamp_ms or 0,
        'duration_ms': build.duration_ms or 0,
        'duration_seconds': build.duration_seconds or 0,
    }


def _serialize_detailed_build(build, branch_name):
    payload = _serialize_branch_build(build, branch_name)
    if payload is None:
        return None

    payload['stages'] = [
        {
            'name': stage.stage_name,
            'status': stage.status,
            'duration_ms': stage.duration_ms or 0,
            'start_time': _datetime_to_millis(stage.started_at),
        }
        for stage in sorted(
            build.stages,
            key=lambda item: (item.started_at or datetime.min.replace(tzinfo=timezone.utc), item.stage_name),
        )
    ]
    return payload


def _build_branch_summary(branch_row, build_rows):
    finished_rows = [row for row in build_rows if row.result is not None]
    successful = sum(1 for row in finished_rows if row.result == 'SUCCESS')
    failed = sum(1 for row in finished_rows if row.result == 'FAILURE')
    aborted = sum(1 for row in finished_rows if row.result == 'ABORTED')
    running = sum(1 for row in build_rows if row.result is None)
    finished_count = successful + failed + aborted

    avg_duration_ms = branch_row.avg_duration_ms
    if avg_duration_ms is None:
        durations = [row.duration_ms for row in finished_rows if row.duration_ms and row.duration_ms > 0]
        avg_duration_ms = int(sum(durations) / len(durations)) if durations else 60000

    avg_duration_seconds = branch_row.avg_duration_seconds
    if avg_duration_seconds is None:
        avg_duration_seconds = int(avg_duration_ms / 1000) if avg_duration_ms else 0

    success_rate = branch_row.success_rate
    if success_rate is None:
        success_rate = round((successful / finished_count) * 100, 1) if finished_count > 0 else 0

    last_build = build_rows[0] if build_rows else None
    last_completed = next((row for row in build_rows if row.result is not None), None)

    return {
        'last_build_number': branch_row.last_build_number or (last_build.build_number if last_build else None),
        'last_completed_build_number': (
            branch_row.last_completed_build_number
            or (last_completed.build_number if last_completed else None)
        ),
        'total_builds': branch_row.total_builds if branch_row.total_builds is not None else len(finished_rows),
        'successful': branch_row.successful_builds if branch_row.successful_builds is not None else successful,
        'failed': branch_row.failed_builds if branch_row.failed_builds is not None else failed,
        'aborted': branch_row.aborted_builds if branch_row.aborted_builds is not None else aborted,
        'running': branch_row.running_builds if branch_row.running_builds is not None else running,
        'success_rate': success_rate,
        'health_score': branch_row.health_score if branch_row.health_score is not None else 0,
        'avg_duration_ms': avg_duration_ms,
        'avg_duration_seconds': avg_duration_seconds,
    }


def _branch_payload_from_row(branch_row, selected_branch):
    build_rows = (
        PipelineBranchBuild.query
        .filter_by(branch_id=branch_row.id)
        .order_by(PipelineBranchBuild.build_number.desc())
        .all()
    )
    summary = _build_branch_summary(branch_row, build_rows)

    last_build_row = next(
        (row for row in build_rows if row.build_number == summary['last_build_number']),
        build_rows[0] if build_rows else None,
    )
    last_completed_row = next(
        (row for row in build_rows if row.build_number == summary['last_completed_build_number']),
        next((row for row in build_rows if row.result is not None), None),
    )

    finished_recent = [row for row in build_rows if row.result is not None][:20]
    trend_rows = list(reversed(finished_recent))

    return {
        'name': branch_row.name,
        'selected': branch_row.name == selected_branch,
        'summary': summary,
        'status': {
            'color': branch_row.status_color,
            'building': bool(branch_row.is_building),
        },
        'last_build': _serialize_branch_build(last_build_row, branch_row.name),
        'last_completed_build': _serialize_branch_build(last_completed_row, branch_row.name),
        'links': {
            'job_url': branch_row.job_url,
        },
        'builds': [
            _serialize_detailed_build(row, branch_row.name)
            for row in build_rows
        ],
        'trends': {
            'builds': [
                _serialize_branch_build(row, branch_row.name)
                for row in build_rows
            ],
            'durations': [
                {
                    'branch': branch_row.name,
                    'number': row.build_number,
                    'duration_seconds': row.duration_seconds or 0,
                    'duration_ms': row.duration_ms or 0,
                }
                for row in trend_rows
            ],
            'coverage': [
                {
                    'branch': branch_row.name,
                    'number': row.build_number,
                    'coverage': row.coverage_percent,
                    'timestamp': row.timestamp_ms or 0,
                }
                for row in trend_rows
            ],
            'junit': [
                {
                    'branch': branch_row.name,
                    'number': row.build_number,
                    'total': row.junit_total,
                    'passed': row.junit_passed,
                    'failed': row.junit_failed,
                    'skipped': row.junit_skipped,
                }
                for row in trend_rows
            ],
        },
        'stages': {
            'failure_rate': {
                row.stage_name: row.failure_rate
                for row in sorted(branch_row.stage_kpis, key=lambda item: item.stage_name)
            },
        },
        'quality': {
            'avg_test_coverage': branch_row.avg_test_coverage,
        },
        'deployment': {
            'frequency': {
                'successful': branch_row.deployment_successful or 0,
                'total': branch_row.deployment_total or 0,
                'rate': branch_row.deployment_rate or 0,
            },
        },
    }


def get_stored_pipeline_kpis():
    pipeline = _load_pipeline_definition()
    if pipeline is None:
        return None

    branch_rows = (
        PipelineBranch.query
        .filter_by(pipeline_id=pipeline.id)
        .order_by(PipelineBranch.is_selected.desc(), PipelineBranch.name.asc())
        .all()
    )
    if not branch_rows:
        return None

    selected_branch = (
        pipeline.selected_branch
        or next((row.name for row in branch_rows if row.is_selected), None)
        or branch_rows[0].name
    )
    branches = {
        row.name: _branch_payload_from_row(row, selected_branch)
        for row in branch_rows
    }

    return {
        'connected': True,
        'pipeline': {
            'name': pipeline.name,
            'type': pipeline.pipeline_type or ('multibranch' if len(branch_rows) > 1 else 'single-branch'),
            'selected_branch': selected_branch,
        },
        'branches': branches,
    }


def get_stored_overview_kpis():
    stored = get_stored_pipeline_kpis()
    if not stored:
        return None

    pipeline = stored.get('pipeline') or {}
    branches = stored.get('branches') or {}
    selected_branch = pipeline.get('selected_branch')
    branch_data = branches.get(selected_branch) if selected_branch else None
    if not branch_data:
        return None

    summary = branch_data.get('summary') or {}
    detailed_builds = branch_data.get('builds') or []
    trend_builds = (branch_data.get('trends') or {}).get('builds') or []
    cutoff_ms = int((_utcnow() - timedelta(hours=24)).timestamp() * 1000)
    source_builds = detailed_builds or trend_builds
    build_trend = [
        {
            'branch': item.get('branch'),
            'number': item.get('number'),
            'result': item.get('result'),
            'timestamp': item.get('timestamp', 0),
            'duration': item.get('duration_ms', item.get('duration', 0)),
            'duration_ms': item.get('duration_ms', item.get('duration', 0)),
            'duration_seconds': item.get('duration_seconds', 0),
            'stages': item.get('stages') or [],
        }
        for item in source_builds
        if item.get('result') is None or (item.get('timestamp', 0) or 0) >= cutoff_ms
    ]

    return {
        'connected': True,
        'last_build_number': summary.get('last_build_number'),
        'total_builds': summary.get('total_builds'),
        'successful': summary.get('successful'),
        'failed': summary.get('failed'),
        'aborted': summary.get('aborted'),
        'running': summary.get('running'),
        'success_rate': summary.get('success_rate'),
        'health_score': summary.get('health_score'),
        'build_trend': build_trend,
        'avg_duration_ms': summary.get('avg_duration_ms'),
    }


def _prepare_branch_build_payloads(branch_payload):
    prepared = {}
    trends = branch_payload.get('trends') or {}
    coverage_map = {
        item.get('number'): item
        for item in (trends.get('coverage') or [])
        if item.get('number') is not None
    }
    junit_map = {
        item.get('number'): item
        for item in (trends.get('junit') or [])
        if item.get('number') is not None
    }

    def ensure_payload(number):
        if number is None:
            return None
        payload = prepared.get(number)
        if payload is None:
            payload = {
                'number': number,
                'result': None,
                'timestamp_ms': 0,
                'duration_seconds': 0,
                'duration_ms': 0,
                'is_last_build': False,
                'is_last_completed_build': False,
                'stages': None,
                'coverage_percent': None,
                'junit_total': None,
                'junit_passed': None,
                'junit_failed': None,
                'junit_skipped': None,
            }
            prepared[number] = payload
        return payload

    for build in (branch_payload.get('builds') or []):
        number = build.get('number')
        payload = ensure_payload(number)
        if payload is None:
            continue
        payload.update({
            'result': build.get('result'),
            'timestamp_ms': build.get('timestamp', 0) or 0,
            'duration_seconds': build.get('duration_seconds', 0) or 0,
            'duration_ms': build.get('duration_ms', 0) or 0,
            'stages': build.get('stages') or [],
        })

    for summary_key, flag_key in (
        ('last_build', 'is_last_build'),
        ('last_completed_build', 'is_last_completed_build'),
    ):
        summary_build = branch_payload.get(summary_key) or {}
        number = summary_build.get('number')
        payload = ensure_payload(number)
        if payload is None:
            continue
        payload.update({
            'result': summary_build.get('result'),
            'timestamp_ms': summary_build.get('timestamp', 0) or 0,
            'duration_seconds': summary_build.get('duration_seconds', 0) or 0,
            'duration_ms': summary_build.get('duration_ms', 0) or 0,
        })
        payload[flag_key] = True

    for number, item in coverage_map.items():
        payload = ensure_payload(number)
        if payload is None:
            continue
        payload['coverage_percent'] = item.get('coverage')

    for number, item in junit_map.items():
        payload = ensure_payload(number)
        if payload is None:
            continue
        payload['junit_total'] = item.get('total')
        payload['junit_passed'] = item.get('passed')
        payload['junit_failed'] = item.get('failed')
        payload['junit_skipped'] = item.get('skipped')

    return prepared


def _sync_branch_stage_kpis(branch_row, branch_payload):
    if 'stages' not in branch_payload:
        return

    stage_failure = ((branch_payload.get('stages') or {}).get('failure_rate') or {})
    existing = {
        row.stage_name: row
        for row in PipelineBranchStageKpi.query.filter_by(branch_id=branch_row.id).all()
    }

    for stage_name, row in list(existing.items()):
        if stage_name not in stage_failure:
            db.session.delete(row)

    for stage_name, failure_rate in stage_failure.items():
        clean_name = (stage_name or '').strip()
        if not clean_name:
            continue

        row = existing.get(clean_name)
        if row is None:
            row = PipelineBranchStageKpi(
                branch_id=branch_row.id,
                stage_name=clean_name,
            )
            db.session.add(row)

        row.failure_rate = failure_rate


def _sync_build_stages(build_row, stages):
    existing = {
        row.stage_name: row
        for row in PipelineBranchBuildStage.query.filter_by(
            pipeline_branch_build_id=build_row.id
        ).all()
    }
    incoming_names = set()

    for stage in stages or []:
        stage_name = (stage.get('name') or '').strip()
        if not stage_name:
            continue

        incoming_names.add(stage_name)
        row = existing.get(stage_name)
        if row is None:
            row = PipelineBranchBuildStage(
                pipeline_branch_build_id=build_row.id,
                stage_name=stage_name,
            )
            db.session.add(row)

        row.status = stage.get('status')
        row.started_at = _millis_to_datetime(stage.get('start_time'))
        row.duration_ms = int(stage.get('duration_ms') or 0)

    for stage_name, row in existing.items():
        if stage_name not in incoming_names:
            db.session.delete(row)


def _sync_branch_builds(branch_row, branch_payload):
    prepared = _prepare_branch_build_payloads(branch_payload)
    if not prepared:
        return

    PipelineBranchBuild.query.filter_by(branch_id=branch_row.id).update(
        {
            PipelineBranchBuild.is_last_build: False,
            PipelineBranchBuild.is_last_completed_build: False,
        },
        synchronize_session=False,
    )

    build_numbers = list(prepared.keys())
    existing = {
        row.build_number: row
        for row in PipelineBranchBuild.query.filter(
            PipelineBranchBuild.branch_id == branch_row.id,
            PipelineBranchBuild.build_number.in_(build_numbers),
        ).all()
    }

    for build_number, payload in prepared.items():
        row = existing.get(build_number)
        if row is None:
            row = PipelineBranchBuild(
                branch_id=branch_row.id,
                build_number=build_number,
            )
            db.session.add(row)

        row.result = payload.get('result')
        row.is_running = payload.get('result') is None
        row.is_last_build = bool(payload.get('is_last_build'))
        row.is_last_completed_build = bool(payload.get('is_last_completed_build'))
        row.timestamp_ms = int(payload.get('timestamp_ms') or 0)
        row.started_at = _millis_to_datetime(payload.get('timestamp_ms'))
        row.duration_seconds = int(payload.get('duration_seconds') or 0)
        row.duration_ms = int(payload.get('duration_ms') or (row.duration_seconds * 1000))
        row.coverage_percent = payload.get('coverage_percent')
        row.junit_total = payload.get('junit_total')
        row.junit_passed = payload.get('junit_passed')
        row.junit_failed = payload.get('junit_failed')
        row.junit_skipped = payload.get('junit_skipped')

        db.session.flush()
        if payload.get('stages') is not None:
            _sync_build_stages(row, payload.get('stages') or [])


def sync_pipeline_snapshot(payload):
    if not payload or not payload.get('connected'):
        return False

    pipeline_payload = payload.get('pipeline') or {}
    branches_payload = payload.get('branches') or {}
    if not branches_payload:
        return False

    now = _utcnow()
    selected_branch = pipeline_payload.get('selected_branch') or _selected_branch_from_config()
    job_path = _pipeline_job_path_from_config()

    try:
        pipeline_row = PipelineDefinition.query.filter_by(
            source_system='jenkins',
            job_path=job_path,
        ).one_or_none()
        if pipeline_row is None:
            pipeline_row = PipelineDefinition(
                source_system='jenkins',
                job_path=job_path,
            )
            db.session.add(pipeline_row)

        pipeline_row.name = pipeline_payload.get('name') or _pipeline_name_from_job_path(job_path)
        pipeline_row.pipeline_type = pipeline_payload.get('type')
        pipeline_row.selected_branch = selected_branch
        pipeline_row.last_synced_at = now
        db.session.flush()

        existing_branches = {
            row.name: row
            for row in PipelineBranch.query.filter_by(pipeline_id=pipeline_row.id).all()
        }

        for branch_name, branch_payload in branches_payload.items():
            row = existing_branches.get(branch_name)
            if row is None:
                row = PipelineBranch(
                    pipeline_id=pipeline_row.id,
                    name=branch_name,
                )
                db.session.add(row)

            summary = branch_payload.get('summary') or {}
            status = branch_payload.get('status') or {}
            links = branch_payload.get('links') or {}
            quality = branch_payload.get('quality') or {}
            deployment = ((branch_payload.get('deployment') or {}).get('frequency') or {})
            last_completed_build = branch_payload.get('last_completed_build') or {}

            row.job_name = branch_payload.get('job_name')
            row.is_selected = branch_name == selected_branch
            row.job_url = links.get('job_url')
            row.status_color = status.get('color')
            row.is_building = bool(status.get('building'))
            row.health_score = summary.get('health_score')
            row.last_build_number = (
                summary.get('last_build_number')
                or ((branch_payload.get('last_build') or {}).get('number'))
            )
            row.last_completed_build_number = (
                summary.get('last_completed_build_number')
                or last_completed_build.get('number')
            )
            row.total_builds = summary.get('total_builds')
            row.successful_builds = summary.get('successful')
            row.failed_builds = summary.get('failed')
            row.aborted_builds = summary.get('aborted')
            row.running_builds = summary.get('running')
            row.success_rate = summary.get('success_rate')
            row.avg_duration_ms = summary.get('avg_duration_ms')
            row.avg_duration_seconds = summary.get('avg_duration_seconds')
            row.avg_test_coverage = quality.get('avg_test_coverage')
            row.deployment_successful = deployment.get('successful')
            row.deployment_total = deployment.get('total')
            row.deployment_rate = deployment.get('rate')
            row.last_synced_at = now

            db.session.flush()
            _sync_branch_stage_kpis(row, branch_payload)
            _sync_branch_builds(row, branch_payload)

        db.session.commit()
        return True
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to sync structured pipeline snapshot to the database.'
        )
        return False


def _load_existing_builds(build_numbers):
    if not build_numbers:
        return {}

    rows = PipelineBuildDuration.query.filter(
        PipelineBuildDuration.build_number.in_(build_numbers)
    ).all()
    return {row.build_number: row for row in rows}


def _load_existing_stages(build_numbers):
    if not build_numbers:
        return {}

    rows = (
        PipelineStageDuration.query
        .join(PipelineBuildDuration)
        .filter(PipelineBuildDuration.build_number.in_(build_numbers))
        .all()
    )
    return {(row.pipeline_build_id, row.stage_name): row for row in rows}


def sync_pipeline_durations(builds):
    build_numbers = [b.get('number') for b in builds if b.get('number') is not None]
    if not build_numbers:
        return

    try:
        existing_builds = _load_existing_builds(build_numbers)

        for build in builds:
            build_number = build.get('number')
            if build_number is None:
                continue

            row = existing_builds.get(build_number)
            if row is None:
                row = PipelineBuildDuration(build_number=build_number)
                db.session.add(row)
                existing_builds[build_number] = row

            duration_seconds = int(build.get('duration') or build.get('duration_seconds') or 0)
            duration_ms = int(build.get('duration_ms') or (duration_seconds * 1000))
            row.result = build.get('result')
            row.started_at = _millis_to_datetime(build.get('timestamp'))
            row.duration_seconds = duration_seconds
            row.duration_ms = duration_ms

        db.session.flush()

        existing_stages = _load_existing_stages(build_numbers)
        for build in builds:
            build_number = build.get('number')
            if build_number is None:
                continue

            build_row = existing_builds[build_number]
            for stage in build.get('stages', []):
                stage_name = (stage.get('name') or '').strip()
                if not stage_name:
                    continue

                key = (build_row.id, stage_name)
                stage_row = existing_stages.get(key)
                if stage_row is None:
                    stage_row = PipelineStageDuration(
                        pipeline_build_id=build_row.id,
                        stage_name=stage_name,
                    )
                    db.session.add(stage_row)
                    existing_stages[key] = stage_row

                stage_row.status = stage.get('status')
                stage_row.started_at = _millis_to_datetime(stage.get('start_time'))
                stage_row.duration_ms = int(stage.get('duration_ms') or 0)

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Failed to sync pipeline and stage durations to the database.'
        )
