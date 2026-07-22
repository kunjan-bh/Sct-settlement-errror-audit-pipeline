from datetime import datetime
from app.extensions import db


class ClassificationRule(db.Model):
    """
    This table IS your OUR_SIDE_RULES / AGGREGATOR_RULES lists from the
    Python snippet you pasted -- just moved from hardcoded Python into
    editable rows, per your "full CRUD via UI" answer.

    side: "sct" | "aggregator" | "bank"
        This is the "whose fault" dimension. Note this is INDEPENDENT of
        PartnerMapping.partner_type. Example: a bank's own endpoint timing
        out is side="bank", but the transaction's partner (from
        PartnerMapping) might still say partner_type="bank" too -- they'll
        usually agree, but they don't have to. A bank could occasionally
        have an aggregator-layer issue, and vice versa.

    match_type: "contains" | "starts_with" | "exact"
        Same three strategies your original function used. Kept identical
        so migrating your existing rule list is a straight copy-paste, not
        a rewrite.

    priority: rules are evaluated in ascending priority order, first match
        wins (mirrors the `break` behavior in your original for-loop, where
        earlier rules in the list took precedence over later ones).

    We deliberately do NOT hardcode a "generic decline" side. In your
    snippet, generic declines were always routed to Aggregator. Here, we
    make that its own rule too (side="aggregator", pattern="failed", etc.)
    so it's just as editable as everything else, instead of a special case
    in code.
    """

    __tablename__ = "classification_rules"

    id = db.Column(db.Integer, primary_key=True)

    side = db.Column(db.String(16), nullable=False)  # "sct" | "aggregator" | "bank"
    match_type = db.Column(db.String(16), nullable=False)  # contains|starts_with|exact
    pattern = db.Column(db.String(256), nullable=False)  # matched against lowercased remark
    category = db.Column(db.String(128), nullable=False)  # human-readable issue name

    active = db.Column(db.Boolean, default=True, nullable=False)
    priority = db.Column(db.Integer, default=100, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def to_dict(self):
        return {
            "id": self.id,
            "side": self.side,
            "match_type": self.match_type,
            "pattern": self.pattern,
            "category": self.category,
            "active": self.active,
            "priority": self.priority,
        }
