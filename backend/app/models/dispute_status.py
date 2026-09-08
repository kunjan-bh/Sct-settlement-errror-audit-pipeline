from datetime import datetime

from app.extensions import db

# What an operator can do with a dispute. "exclude" means "not our problem to
# chase" -- a test merchant, an aggregator that settles on its own schedule --
# and is deliberately not a kind of solved: excluded disputes are counted
# separately and drawn nowhere, the same rule the batch flow follows.
DISPUTE_STATUSES = ("pending", "in_progress", "solved", "exclude")


class DisputeStatus(db.Model):
    """
    One operator decision about one failed settlement on the switch.

    The disputes themselves are never stored -- they are read live from
    fund_transfer_logs each time the tab is opened, so they always reflect the
    switch rather than a stale copy. What *is* ours to keep is the decision:
    who looked at it, what they concluded, and what they wrote down.

    Keyed by `dispute_key`, the switch's own fund_transfer_logs.id. That id is
    stable and unique per settlement attempt, so a decision stays attached to
    its settlement across refreshes and date-range changes -- unlike a
    positional or (mid, amount) key, which would silently re-point at a
    different failure the next time something was retried.

    scope_type/scope_value record an exclusion made against a whole entity
    rather than one row: excluding "Mbank" as a wallet marks every one of its
    failures rather than making the operator click 40 of them. A row-level
    decision leaves both null.
    """

    __tablename__ = "dispute_statuses"

    id = db.Column(db.Integer, primary_key=True)

    # fund_transfer_logs.id from the switch. Text, because the switch's ids are.
    dispute_key = db.Column(db.String(128), nullable=False, index=True)

    # Denormalised from the switch so a decision is still readable when the
    # settlement has aged out of the range being viewed.
    mid = db.Column(db.String(32), nullable=True)
    crrn = db.Column(db.String(64), nullable=True)
    amount = db.Column(db.Float, nullable=True)
    partner_name = db.Column(db.String(64), nullable=True)

    status = db.Column(db.String(16), nullable=False, default="pending")
    comment = db.Column(db.Text, nullable=True)

    # The office session this decision was taken in. Stamped automatically
    # when the decision is recorded, so nobody has to file anything by hand --
    # working the list IS filling in the batch.
    batch_id = db.Column(db.Integer, db.ForeignKey("batches.id"), nullable=True, index=True)

    # Entity-level exclusions: ("partner", "Mbank"), ("bank_or_wallet", "..."),
    # ("mid", "004..."). Null for a decision about this one settlement.
    scope_type = db.Column(db.String(24), nullable=True)
    scope_value = db.Column(db.String(128), nullable=True)

    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint("dispute_key", name="uq_dispute_key"),
        db.Index("ix_dispute_scope", "scope_type", "scope_value"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "dispute_key": self.dispute_key,
            "mid": self.mid,
            "crrn": self.crrn,
            "amount": self.amount,
            "partner_name": self.partner_name,
            "status": self.status,
            "comment": self.comment,
            "batch_id": self.batch_id,
            "scope_type": self.scope_type,
            "scope_value": self.scope_value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
