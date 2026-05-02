from extensions import db


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
