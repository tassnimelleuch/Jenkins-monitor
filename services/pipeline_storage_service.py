from datetime import datetime, timezone

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from pipeline_storage_models import PipelineBuildDuration, PipelineStageDuration


def _millis_to_datetime(value):
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


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

            duration_seconds = int(build.get('duration') or 0)
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
