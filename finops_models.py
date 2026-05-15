from datetime import datetime, timezone

from extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class FinOpsDailyCost(db.Model):
    __tablename__ = 'finops_daily_costs'
    __table_args__ = (
        db.UniqueConstraint(
            'subscription_id',
            'cost_mode',
            'usage_date',
            name='uq_finops_daily_cost_subscription_mode_date',
        ),
        db.CheckConstraint(
            "cost_mode IN ('actual', 'forecast')",
            name='ck_finops_daily_costs_mode',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.String(64), nullable=False, index=True)
    usage_date = db.Column(db.Date, nullable=False, index=True)
    cost_mode = db.Column(db.String(16), nullable=False, default='actual', index=True)
    currency_code = db.Column(db.String(16), nullable=False, default='USD')
    aks_cost = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    vm_cost = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    other_cost = db.Column(db.Numeric(18, 4), nullable=False, default=0)
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


class FinOpsResourceGroupMonthlyCost(db.Model):
    __tablename__ = 'finops_resource_group_monthly_costs'
    __table_args__ = (
        db.UniqueConstraint(
            'subscription_id',
            'year',
            'month',
            'cost_type',
            'resource_group_name',
            name='uq_finops_rg_monthly_cost_scope',
        ),
        db.CheckConstraint(
            'month >= 1 AND month <= 12',
            name='ck_finops_rg_monthly_costs_month',
        ),
        db.CheckConstraint(
            "cost_type IN ('ActualCost', 'AmortizedCost')",
            name='ck_finops_rg_monthly_costs_type',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.String(64), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.Integer, nullable=False, index=True)
    cost_type = db.Column(db.String(32), nullable=False, default='ActualCost', index=True)
    resource_group_name = db.Column(db.String(255), nullable=False, index=True)
    currency_code = db.Column(db.String(16), nullable=False, default='USD')
    aks_cost = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    vm_cost = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    other_cost = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    total_cost = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    by_resource_type = db.Column(db.JSON, nullable=False, default=dict)
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
            "dataset IN ('daily_costs', 'resource_group_costs')",
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
