from extensions import db


class PipelineDefinition(db.Model):
    __tablename__ = 'pipeline_definitions'
    __table_args__ = (
        db.UniqueConstraint(
            'source_system',
            'job_path',
            name='uq_pipeline_definition_source_job'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    source_system = db.Column(db.String(32), nullable=False, default='jenkins', index=True)
    name = db.Column(db.String(255), nullable=False)
    job_path = db.Column(db.String(512), nullable=False)
    pipeline_type = db.Column(db.String(64), nullable=True)
    selected_branch = db.Column(db.String(255), nullable=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)

    branches = db.relationship(
        'PipelineBranch',
        back_populates='pipeline',
        cascade='all, delete-orphan',
        lazy=True,
    )


class PipelineBranch(db.Model):
    __tablename__ = 'pipeline_branches'
    __table_args__ = (
        db.UniqueConstraint(
            'pipeline_id',
            'name',
            name='uq_pipeline_branch_pipeline_name'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    pipeline_id = db.Column(
        db.Integer,
        db.ForeignKey('pipeline_definitions.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(255), nullable=False)
    job_name = db.Column(db.String(255), nullable=True)
    is_selected = db.Column(db.Boolean, nullable=False, default=False)
    job_url = db.Column(db.String(1024), nullable=True)
    status_color = db.Column(db.String(64), nullable=True)
    is_building = db.Column(db.Boolean, nullable=False, default=False)
    health_score = db.Column(db.Integer, nullable=True)
    last_build_number = db.Column(db.Integer, nullable=True)
    last_completed_build_number = db.Column(db.Integer, nullable=True)
    total_builds = db.Column(db.Integer, nullable=True)
    successful_builds = db.Column(db.Integer, nullable=True)
    failed_builds = db.Column(db.Integer, nullable=True)
    aborted_builds = db.Column(db.Integer, nullable=True)
    running_builds = db.Column(db.Integer, nullable=True)
    success_rate = db.Column(db.Float, nullable=True)
    avg_duration_ms = db.Column(db.BigInteger, nullable=True)
    avg_duration_seconds = db.Column(db.Integer, nullable=True)
    avg_test_coverage = db.Column(db.Float, nullable=True)
    deployment_successful = db.Column(db.Integer, nullable=True)
    deployment_total = db.Column(db.Integer, nullable=True)
    deployment_rate = db.Column(db.Float, nullable=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)

    pipeline = db.relationship(
        'PipelineDefinition',
        back_populates='branches',
    )
    stage_kpis = db.relationship(
        'PipelineBranchStageKpi',
        back_populates='branch',
        cascade='all, delete-orphan',
        lazy=True,
    )
    builds = db.relationship(
        'PipelineBranchBuild',
        back_populates='branch',
        cascade='all, delete-orphan',
        lazy=True,
    )


class PipelineBranchStageKpi(db.Model):
    __tablename__ = 'pipeline_branch_stage_kpis'
    __table_args__ = (
        db.UniqueConstraint(
            'branch_id',
            'stage_name',
            name='uq_pipeline_branch_stage_kpi'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(
        db.Integer,
        db.ForeignKey('pipeline_branches.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    stage_name = db.Column(db.String(255), nullable=False)
    failure_rate = db.Column(db.Float, nullable=True)

    branch = db.relationship(
        'PipelineBranch',
        back_populates='stage_kpis',
    )


class PipelineBranchBuild(db.Model):
    __tablename__ = 'pipeline_branch_builds'
    __table_args__ = (
        db.UniqueConstraint(
            'branch_id',
            'build_number',
            name='uq_pipeline_branch_build'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(
        db.Integer,
        db.ForeignKey('pipeline_branches.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    build_number = db.Column(db.Integer, nullable=False)
    result = db.Column(db.String(32), nullable=True)
    is_running = db.Column(db.Boolean, nullable=False, default=False)
    is_last_build = db.Column(db.Boolean, nullable=False, default=False)
    is_last_completed_build = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    timestamp_ms = db.Column(db.BigInteger, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    duration_ms = db.Column(db.BigInteger, nullable=False, default=0)
    coverage_percent = db.Column(db.Float, nullable=True)
    junit_total = db.Column(db.Integer, nullable=True)
    junit_passed = db.Column(db.Integer, nullable=True)
    junit_failed = db.Column(db.Integer, nullable=True)
    junit_skipped = db.Column(db.Integer, nullable=True)

    branch = db.relationship(
        'PipelineBranch',
        back_populates='builds',
    )
    stages = db.relationship(
        'PipelineBranchBuildStage',
        back_populates='build',
        cascade='all, delete-orphan',
        lazy=True,
    )


class PipelineBranchBuildStage(db.Model):
    __tablename__ = 'pipeline_branch_build_stages'
    __table_args__ = (
        db.UniqueConstraint(
            'pipeline_branch_build_id',
            'stage_name',
            name='uq_pipeline_branch_build_stage'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    pipeline_branch_build_id = db.Column(
        db.Integer,
        db.ForeignKey('pipeline_branch_builds.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    stage_name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(32), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    duration_ms = db.Column(db.BigInteger, nullable=False, default=0)

    build = db.relationship(
        'PipelineBranchBuild',
        back_populates='stages',
    )


class PipelineBuildDuration(db.Model):
    __tablename__ = 'pipeline_build_durations'

    id = db.Column(db.Integer, primary_key=True)
    build_number = db.Column(db.Integer, unique=True, nullable=False, index=True)
    result = db.Column(db.String(32), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    duration_ms = db.Column(db.BigInteger, nullable=False, default=0)

    stages = db.relationship(
        'PipelineStageDuration',
        back_populates='pipeline_build',
        cascade='all, delete-orphan',
        lazy=True,
    )


class PipelineStageDuration(db.Model):
    __tablename__ = 'pipeline_stage_durations'
    __table_args__ = (
        db.UniqueConstraint(
            'pipeline_build_id',
            'stage_name',
            name='uq_pipeline_stage_duration'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    pipeline_build_id = db.Column(
        db.Integer,
        db.ForeignKey('pipeline_build_durations.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    stage_name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(32), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    duration_ms = db.Column(db.BigInteger, nullable=False, default=0)

    pipeline_build = db.relationship(
        'PipelineBuildDuration',
        back_populates='stages',
    )
