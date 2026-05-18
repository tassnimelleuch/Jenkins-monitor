from datetime import datetime, timezone

from extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class DashboardKpiDocument(db.Model):
    __tablename__ = 'dashboard_kpi_documents'
    __table_args__ = (
        db.UniqueConstraint(
            'pipeline_job_path',
            'branch_name',
            'document_key',
            name='uq_dashboard_kpi_document_scope',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    pipeline_job_path = db.Column(db.String(512), nullable=False, default='', index=True)
    pipeline_name = db.Column(db.String(255), nullable=False, default='Jenkins Pipeline')
    branch_name = db.Column(db.String(255), nullable=False, default='main', index=True)
    document_key = db.Column(db.String(128), nullable=False, index=True)
    dashboard_page = db.Column(db.String(64), nullable=False, default='shared', index=True)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    summary = db.Column(db.JSON, nullable=False, default=dict)
    source_system = db.Column(
        db.String(32),
        nullable=False,
        default='dashboard_kpis_rag',
    )
    last_generated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    chunks = db.relationship(
        'DashboardKpiDocumentChunk',
        back_populates='document',
        cascade='all, delete-orphan',
        lazy=True,
        order_by=lambda: (
            DashboardKpiDocumentChunk.chunk_index.asc(),
            DashboardKpiDocumentChunk.id.asc(),
        ),
    )


class DashboardKpiDocumentChunk(db.Model):
    __tablename__ = 'dashboard_kpi_document_chunks'
    __table_args__ = (
        db.UniqueConstraint(
            'document_id',
            'chunk_index',
            name='uq_dashboard_kpi_document_chunk_order',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(
        db.Integer,
        db.ForeignKey('dashboard_kpi_documents.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    pipeline_job_path = db.Column(db.String(512), nullable=False, default='', index=True)
    pipeline_name = db.Column(db.String(255), nullable=False, default='Jenkins Pipeline')
    branch_name = db.Column(db.String(255), nullable=False, default='main', index=True)
    document_key = db.Column(db.String(128), nullable=False, index=True)
    dashboard_page = db.Column(db.String(64), nullable=False, default='shared', index=True)
    chunk_index = db.Column(db.Integer, nullable=False)
    chunk_count = db.Column(db.Integer, nullable=False, default=0)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    summary = db.Column(db.JSON, nullable=False, default=dict)
    source_system = db.Column(
        db.String(32),
        nullable=False,
        default='dashboard_kpis_rag',
    )
    last_generated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    document = db.relationship(
        'DashboardKpiDocument',
        back_populates='chunks',
    )
