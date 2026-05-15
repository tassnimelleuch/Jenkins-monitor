from datetime import datetime, timezone

from extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class PersistentAlert(db.Model):
    __tablename__ = 'persistent_alerts'
    __table_args__ = (
        db.UniqueConstraint(
            'alert_key',
            name='uq_persistent_alert_key',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    alert_key = db.Column(db.String(255), nullable=False, unique=True, index=True)
    rule_id = db.Column(db.String(64), nullable=False, index=True)
    source_system = db.Column(db.String(32), nullable=False, default='finops', index=True)
    severity = db.Column(db.String(16), nullable=False, default='warning')
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    resource_scope = db.Column(db.String(32), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.Integer, nullable=False, index=True)
    usage_date = db.Column(db.Date, nullable=True, index=True)
    currency_code = db.Column(db.String(16), nullable=False, default='USD')
    current_value = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    threshold_value = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    delta_value = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    is_checked = db.Column(db.Boolean, nullable=False, default=False, index=True)
    checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    checked_by_username = db.Column(db.String(80), nullable=True)
    first_detected_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    last_detected_at = db.Column(
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
