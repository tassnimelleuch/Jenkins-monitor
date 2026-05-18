from datetime import datetime, timezone

from sqlalchemy import inspect, text

from extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class FinOpsDailyCost(db.Model):
    __tablename__ = 'finops_daily_costs'
    __table_args__ = (
        db.UniqueConstraint(
            'subscription_id',
            'usage_date',
            name='uq_finops_daily_cost_subscription_date',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.String(64), nullable=False, index=True)
    usage_date = db.Column(db.Date, nullable=False, index=True)
    currency_code = db.Column(db.String(16), nullable=False, default='USD')
    total_cost = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    source_system = db.Column(
        db.String(32),
        nullable=False,
        default='azure_cost_management',
    )
    last_synced_at = db.Column(
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


class FinOpsSyncState(db.Model):
    __tablename__ = 'finops_sync_states'
    __table_args__ = (
        db.UniqueConstraint(
            'subscription_id',
            'dataset',
            'year',
            'month',
            'scope_key',
            name='uq_finops_sync_state_scope',
        ),
        db.CheckConstraint(
            "dataset IN ('daily_costs')",
            name='ck_finops_sync_states_dataset',
        ),
        db.CheckConstraint(
            'month >= 1 AND month <= 12',
            name='ck_finops_sync_states_month',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.String(64), nullable=False, index=True)
    dataset = db.Column(db.String(32), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.Integer, nullable=False, index=True)
    scope_key = db.Column(db.String(64), nullable=False, index=True)
    source_system = db.Column(
        db.String(32),
        nullable=False,
        default='azure_cost_management',
    )
    last_attempted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )


class FinOpsBuildDocument(db.Model):
    __tablename__ = 'finops_builds_documents'
    __table_args__ = (
        db.UniqueConstraint(
            'subscription_id',
            'usage_date',
            'pipeline_job_path',
            name='uq_finops_build_document_scope',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.String(64), nullable=False, default='', index=True)
    usage_date = db.Column(db.Date, nullable=False, index=True)
    pipeline_job_path = db.Column(db.String(512), nullable=False, default='', index=True)
    pipeline_name = db.Column(db.String(255), nullable=False, default='Jenkins Pipeline')
    currency_code = db.Column(db.String(16), nullable=False, default='USD')
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    summary = db.Column(db.JSON, nullable=False, default=dict)
    source_system = db.Column(
        db.String(32),
        nullable=False,
        default='finops_builds_rag',
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


EXPECTED_FINOPS_DAILY_COLUMNS = {
    'id',
    'subscription_id',
    'usage_date',
    'currency_code',
    'total_cost',
    'source_system',
    'last_synced_at',
    'created_at',
    'updated_at',
}

LEGACY_FINOPS_DAILY_COLUMNS = {
    'cost_mode',
    'aks_cost',
    'vm_cost',
    'other_cost',
}

OBSOLETE_FINOPS_TABLES = (
    'finops_resource_group_monthly_costs',
)


def _table_columns(inspector, table_name):
    if not inspector.has_table(table_name):
        return set()
    return {column['name'] for column in inspector.get_columns(table_name)}


def _has_expected_daily_constraint(inspector):
    expected = {'subscription_id', 'usage_date'}
    for constraint in inspector.get_unique_constraints(FinOpsDailyCost.__tablename__):
        if set(constraint.get('column_names') or []) == expected:
            return True
    return False


def _legacy_total_cost_expression(existing_columns):
    if {'aks_cost', 'vm_cost', 'other_cost'} & set(existing_columns):
        return (
            'COALESCE(total_cost, '
            'COALESCE(aks_cost, 0) + COALESCE(vm_cost, 0) + COALESCE(other_cost, 0), '
            '0)'
        )
    return 'COALESCE(total_cost, 0)'


def _rebuild_daily_costs_table(connection, existing_columns):
    legacy_table = f'{FinOpsDailyCost.__tablename__}_legacy'
    total_cost_expression = _legacy_total_cost_expression(existing_columns)
    filter_clause = ''
    if 'cost_mode' in existing_columns:
        filter_clause = "WHERE COALESCE(cost_mode, 'actual') = 'actual'"

    connection.execute(text(f'DROP TABLE IF EXISTS {legacy_table} CASCADE'))
    connection.execute(
        text(
            f'''
            CREATE TABLE {legacy_table} AS
            SELECT *
            FROM {FinOpsDailyCost.__tablename__}
            '''
        )
    )
    connection.execute(text(f'DROP TABLE IF EXISTS {FinOpsDailyCost.__tablename__} CASCADE'))
    FinOpsDailyCost.__table__.create(bind=connection)

    if {'subscription_id', 'usage_date'} <= set(existing_columns):
        connection.execute(
            text(
                f'''
                INSERT INTO {FinOpsDailyCost.__tablename__} (
                    subscription_id,
                    usage_date,
                    currency_code,
                    total_cost,
                    source_system,
                    last_synced_at,
                    created_at,
                    updated_at
                )
                SELECT
                    subscription_id,
                    usage_date,
                    COALESCE(MAX(currency_code), 'USD'),
                    ROUND(SUM({total_cost_expression})::numeric, 4),
                    COALESCE(MAX(source_system), 'azure_cost_management'),
                    MAX(last_synced_at),
                    COALESCE(MIN(created_at), NOW()),
                    COALESCE(MAX(updated_at), NOW())
                FROM {legacy_table}
                {filter_clause}
                GROUP BY subscription_id, usage_date
                '''
            )
        )

    connection.execute(text(f'DROP TABLE IF EXISTS {legacy_table} CASCADE'))


def ensure_finops_storage_schema():
    changed = False
    inspector = inspect(db.engine)

    with db.engine.begin() as connection:
        for table_name in OBSOLETE_FINOPS_TABLES:
            if inspector.has_table(table_name):
                connection.execute(text(f'DROP TABLE IF EXISTS {table_name} CASCADE'))
                changed = True

    inspector = inspect(db.engine)
    table_name = FinOpsDailyCost.__tablename__

    if not inspector.has_table(table_name):
        FinOpsDailyCost.__table__.create(bind=db.engine, checkfirst=True)
        changed = True
    else:
        existing_columns = _table_columns(inspector, table_name)
        needs_rebuild = (
            not EXPECTED_FINOPS_DAILY_COLUMNS.issubset(existing_columns)
            or bool(existing_columns & LEGACY_FINOPS_DAILY_COLUMNS)
            or not _has_expected_daily_constraint(inspector)
        )
        if needs_rebuild:
            with db.engine.begin() as connection:
                _rebuild_daily_costs_table(connection, existing_columns)
            changed = True

    FinOpsSyncState.__table__.create(bind=db.engine, checkfirst=True)
    FinOpsBuildDocument.__table__.create(bind=db.engine, checkfirst=True)
    return changed
