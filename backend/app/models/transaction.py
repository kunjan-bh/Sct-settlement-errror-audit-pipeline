from datetime import datetime
from app.extensions import db


class Transaction(db.Model):
    """
    One row per settlement record from the uploaded Excel.

    Your spec says "Do NOT delete original information... create processed
    copies while preserving original data." Two design choices follow from
    that:

    1. We keep the known columns (mid, merchant_name, remark, etc.) as real
       typed columns -- this is what lets us filter/aggregate fast in SQL
       (e.g. "count failed transactions per aggregator") without parsing
       JSON every query.

    2. Any OTHER columns your Excel happens to have that day (you said
       "there may be additional columns") get dumped verbatim into
       `extra_data` as JSON. Nothing is ever dropped, but we don't force
       the schema to change every time an input file has one new column.

    error_side / error_category / matched_rule_id are the OUTPUT of the
    classification service, not something the user types in. solved_status
    and solved_remark, by contrast, are NOT stored per-transaction -- see
    IssueStatus. Two transactions with the same (batch, side, partner,
    category) share ONE status/comment, because in your workflow you
    resolve an ISSUE ("Mbank wallet validation failed"), not one row at a
    time out of 22 identical ones. We denormalize the resolved status back
    onto each transaction only at report-generation time.
    """

    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("batches.id"), nullable=False, index=True)

    # --- known/expected columns from the input excel ---
    mid = db.Column(db.String(64), nullable=True, index=True)
    merchant_name = db.Column(db.String(256), nullable=True)
    merchant_info = db.Column(db.String(256), nullable=True)
    status = db.Column(db.String(64), nullable=True)
    status_code = db.Column(db.String(32), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    settlement_status = db.Column(db.String(64), nullable=True)
    transaction_status = db.Column(db.String(64), nullable=True)

    # any columns not modeled above -- preserved as JSON, nothing is lost
    extra_data = db.Column(db.JSON, nullable=True)

    # --- resolved partner (from PartnerMapping, computed at ingest time) ---
    partner_name = db.Column(db.String(64), nullable=True, index=True)
    partner_type = db.Column(db.String(16), nullable=True)  # "aggregator" | "bank" | None

    # --- resolved classification (from ClassificationRule, computed at ingest time) ---
    error_side = db.Column(db.String(16), nullable=True, index=True)  # sct|aggregator|bank|unknown
    error_category = db.Column(db.String(128), nullable=True, index=True)
    matched_rule_id = db.Column(db.Integer, db.ForeignKey("classification_rules.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "mid": self.mid,
            "merchant_name": self.merchant_name,
            "merchant_info": self.merchant_info,
            "status": self.status,
            "status_code": self.status_code,
            "remark": self.remark,
            "settlement_status": self.settlement_status,
            "transaction_status": self.transaction_status,
            "extra_data": self.extra_data,
            "partner_name": self.partner_name,
            "partner_type": self.partner_type,
            "error_side": self.error_side,
            "error_category": self.error_category,
        }
