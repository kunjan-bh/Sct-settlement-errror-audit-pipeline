from datetime import datetime
from app.extensions import db


def normalize_member_code(raw: str) -> str:
    """
    Member codes are always 3 characters. If the source gives you a 2-char
    code, left-pad with a zero. This is the exact rule you specified --
    kept as one function so it's applied identically everywhere (import,
    manual entry, resolution at classification time) instead of being
    re-implemented three times with three chances to get it wrong.
    """
    raw = (raw or "").strip()
    if len(raw) == 2:
        return "0" + raw
    return raw


class PartnerMapping(db.Model):
    """
    One row = one 3-char MID member code resolved to a partner.

    Two distinct shapes live in this same table, distinguished by `bucket`:

    1. bucket="aggregator": many small cooperatives settle THROUGH one
       aggregator. Many rows can share the same `partner_name` (the
       aggregator), each with its own unique `member_code` (one coop).
       e.g. member_code="235" -> partner_name="SomeAggregatorX",
            member_code="059" -> partner_name="SomeAggregatorX"
       This is what your JSON import populates in bulk: you pick one
       aggregator name, paste/import a list of member codes, and every
       code in that list gets a row with that same partner_name.

    2. bucket="bank_wallet": a direct 1:1 entity, no grouping. Each row's
       member_code maps to exactly one partner_name that IS the bank/wallet
       itself (NIC ASIA Bank, Global IME Bank, IME Khalti, CityPay, MORU,
       My Pay, Cellcom, Namaste Pay). Added/edited one at a time, not
       imported in bulk, since there's no "many coops under one bank" case.

    `member_code` is unique across the whole table (a given MID prefix
    can't simultaneously belong to an aggregator AND be its own bank/wallet)
    -- enforced at the DB level so a bad import can't silently create
    ambiguous mappings.
    """

    __tablename__ = "partner_mappings"

    id = db.Column(db.Integer, primary_key=True)

    member_code = db.Column(db.String(3), unique=True, nullable=False, index=True)
    bucket = db.Column(db.String(16), nullable=False)  # "aggregator" | "bank_wallet"
    partner_name = db.Column(db.String(128), nullable=False)

    # Optional human-readable note, e.g. the coop's own name from the
    # Excel's Acquirer Name column, purely for display when you're
    # reviewing the mapping list -- not used in any matching logic.
    institution_label = db.Column(db.String(256), nullable=True)

    active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def to_dict(self):
        return {
            "id": self.id,
            "member_code": self.member_code,
            "bucket": self.bucket,
            "partner_name": self.partner_name,
            "institution_label": self.institution_label,
            "active": self.active,
        }
