from datetime import datetime
from app.extensions import db


class IssueStatus(db.Model):
    """
    One row per distinct ISSUE within a batch, where an "issue" is the
    group key (batch, side, partner_name, category) -- e.g.
    "Batch_2026_07_19 / aggregator / Mbank / Wallet Validation Failed".

    This is what backs your "Operations Controls" requirement: a dropdown
    (Solved/Pending/In Progress) and a comment box PER ISSUE CARD, not per
    transaction row. 22 rows that all failed with "Wallet Validation
    Failed" at Mbank share this single status row.

    partner_name is nullable because SCT-side issues have no partner --
    "Response parsing/decode error" is just SCT's problem, full stop.

    unique(batch_id, side, partner_name, category) means the service layer
    does get-or-create when a batch is processed or re-classified, instead
    of accumulating duplicate status rows every time you re-run
    classification after editing rules.
    """

    __tablename__ = "issue_statuses"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("batches.id"), nullable=False, index=True)

    side = db.Column(db.String(16), nullable=False)  # sct | aggregator | bank
    partner_name = db.Column(db.String(64), nullable=True)  # null for sct-side issues
    category = db.Column(db.String(128), nullable=False)
    txn_status = db.Column(db.String(32), nullable=False, default="unknown")

    status = db.Column(db.String(16), nullable=False, default="pending")  # pending|in_progress|solved
    comment = db.Column(db.Text, nullable=True)
    mid_overrides = db.Column(db.JSON, nullable=True, default={})

    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint(
            "batch_id", "side", "partner_name", "category", "txn_status", name="uq_issue_identity"
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "side": self.side,
            "partner_name": self.partner_name,
            "category": self.category,
            "txn_status": self.txn_status,
            "status": self.status,
            "comment": self.comment,
            "mid_overrides": self.mid_overrides or {},
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
