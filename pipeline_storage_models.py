from sqlalchemy import inspect, text

from extensions import db


class PipelineBranch(db.Model):
    __tablename__ = 'pipeline_branches'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True, index=True)
    job_name = db.Column(db.String(255), nullable=True)
    job_url = db.Column(db.String(1024), nullable=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    status_color = db.Column(db.String(64), nullable=True)
    is_building = db.Column(db.Boolean, nullable=False, default=False)
    health_score = db.Column(db.Integer, nullable=True)
    last_build_number = db.Column(db.Integer, nullable=True)
    last_build_result = db.Column(db.String(32), nullable=True)
    last_build_timestamp_ms = db.Column(db.BigInteger, nullable=True)
    last_build_duration_ms = db.Column(db.BigInteger, nullable=True)
    last_completed_build_number = db.Column(db.Integer, nullable=True)
    last_completed_build_result = db.Column(db.String(32), nullable=True)
    last_completed_build_timestamp_ms = db.Column(db.BigInteger, nullable=True)
    last_completed_build_duration_ms = db.Column(db.BigInteger, nullable=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    main_builds = db.relationship(
        'PipelineMainBuild',
        back_populates='branch',
        cascade='all, delete-orphan',
        lazy=True,
    )


class PipelineMainBuild(db.Model):
    __tablename__ = 'pipeline_main_builds'

    build_number = db.Column(db.Integer, primary_key=True)
    id = db.Column(db.Integer, db.Identity(), nullable=False, unique=True, index=True)
    branch_name = db.Column(
        db.String(255),
        db.ForeignKey('pipeline_branches.name', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        default='main',
    )
    status = db.Column(db.String(32), nullable=True)
    result = db.Column(db.String(32), nullable=True)
    is_running = db.Column(db.Boolean, nullable=False, default=False)
    is_last_build = db.Column(db.Boolean, nullable=False, default=False)
    is_last_completed_build = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ended_at = db.Column(db.DateTime(timezone=True), nullable=True)
    timestamp_ms = db.Column(db.BigInteger, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    duration_ms = db.Column(db.BigInteger, nullable=False, default=0)
    coverage_percent = db.Column(db.Float, nullable=True)
    junit_total = db.Column(db.Integer, nullable=True)
    junit_passed = db.Column(db.Integer, nullable=True)
    junit_failed = db.Column(db.Integer, nullable=True)
    junit_skipped = db.Column(db.Integer, nullable=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    branch = db.relationship(
        'PipelineBranch',
        back_populates='main_builds',
    )
    stages = db.relationship(
        'PipelineMainBuildStage',
        back_populates='build',
        cascade='all, delete-orphan',
        lazy=True,
        order_by=lambda: (
            PipelineMainBuildStage.started_at.asc(),
            PipelineMainBuildStage.id.asc(),
        ),
    )


class PipelineMainBuildStage(db.Model):
    __tablename__ = 'pipeline_main_build_stages'
    __table_args__ = (
        db.UniqueConstraint(
            'build_number',
            'stage_name',
            name='uq_pipeline_main_build_stage',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    build_number = db.Column(
        db.Integer,
        db.ForeignKey('pipeline_main_builds.build_number', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    stage_name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(32), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    duration_ms = db.Column(db.BigInteger, nullable=False, default=0)

    build = db.relationship(
        'PipelineMainBuild',
        back_populates='stages',
    )


PIPELINE_SCHEMA_TABLES = (
    'pipeline_main_build_stages',
    'pipeline_main_builds',
    'pipeline_branches',
)

LEGACY_PIPELINE_TABLES = (
    'pipeline_main_build_stages',
    'pipeline_main_builds',
    'pipeline_branch_stage_kpis',
    'pipeline_branch_build_stages',
    'pipeline_branch_builds',
    'pipeline_branches',
    'pipeline_definitions',
    'pipeline_build_durations',
    'pipeline_stage_durations',
)

EXPECTED_PIPELINE_COLUMNS = {
    'pipeline_branches': {
        'id',
        'name',
        'job_name',
        'job_url',
        'is_primary',
        'status_color',
        'is_building',
        'health_score',
        'last_build_number',
        'last_build_result',
        'last_build_timestamp_ms',
        'last_build_duration_ms',
        'last_completed_build_number',
        'last_completed_build_result',
        'last_completed_build_timestamp_ms',
        'last_completed_build_duration_ms',
        'last_synced_at',
    },
    'pipeline_main_builds': {
        'build_number',
        'id',
        'branch_name',
        'status',
        'result',
        'is_running',
        'is_last_build',
        'is_last_completed_build',
        'started_at',
        'ended_at',
        'timestamp_ms',
        'duration_seconds',
        'duration_ms',
        'coverage_percent',
        'junit_total',
        'junit_passed',
        'junit_failed',
        'junit_skipped',
        'last_synced_at',
    },
    'pipeline_main_build_stages': {
        'id',
        'build_number',
        'stage_name',
        'status',
        'started_at',
        'duration_ms',
    },
}


def _table_columns(inspector, table_name):
    if not inspector.has_table(table_name):
        return set()
    return {column['name'] for column in inspector.get_columns(table_name)}


def pipeline_storage_schema_is_current():
    inspector = inspect(db.engine)
    return all(
        EXPECTED_PIPELINE_COLUMNS[table_name].issubset(_table_columns(inspector, table_name))
        for table_name in EXPECTED_PIPELINE_COLUMNS
    )


def ensure_pipeline_storage_schema():
    if pipeline_storage_schema_is_current():
        return False

    with db.engine.begin() as connection:
        for table_name in LEGACY_PIPELINE_TABLES:
            connection.execute(text(f'DROP TABLE IF EXISTS {table_name} CASCADE'))

    PipelineBranch.__table__.create(bind=db.engine, checkfirst=True)
    PipelineMainBuild.__table__.create(bind=db.engine, checkfirst=True)
    PipelineMainBuildStage.__table__.create(bind=db.engine, checkfirst=True)
    return True
