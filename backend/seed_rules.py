"""
One-time seed: loads your existing classification rules (from the Python
function you pasted) into the classification_rules table, and a couple of
placeholder partner mappings so the engine has something to resolve
against. Run this once after the tables are created:

    python seed_rules.py

Re-running is safe -- it clears and re-inserts rather than duplicating.
"""
from app import create_app
from app.extensions import db
from app.models.classification_rule import ClassificationRule
from app.models.partner_mapping import PartnerMapping

app = create_app()

# side, match_type, pattern, category, priority
# Priority mirrors your original evaluation order: SCT rules first,
# then aggregator rules, then generic declines last (lowest priority
# = checked last, since the original code only fell through to generic
# decline when nothing else matched).
RULES = [
    # --- SCT side (from OUR_SIDE_RULES) ---
    ("sct", "contains", "connection refused: interpay.interpay.svc.cluster.local",
     "Internal service unreachable (Interpay)", 10),
    ("sct", "contains", "timeout period of 60000ms",
     "Internal service timeout (Interpay)", 11),
    ("sct", "starts_with", "bank and branch not mapped",
     "Bank/branch not mapped", 12),
    ("sct", "contains", "failed to decode:unrecognized field",
     "Response parsing/decode error", 13),

    # --- Aggregator side (from AGGREGATOR_RULES) ---
    ("aggregator", "contains", "/api/smartqr/transfer",
     "Bank SmartQR transfer API Internal Server Error", 20),
    ("aggregator", "contains", "host error ::unexpected status code: 500",
     "Aggregator Internal Server Error", 21),
    ("bank", "contains", "destination branch operation date issue",
     "MFI branch EOD/date mismatch", 22),
    ("bank", "contains", "head office and destination branch are not in same date",
     "MFI HO/Branch date mismatch", 23),
    ("aggregator", "contains", '"errors":["invalid data"]',
     "Aggregator invalid data", 24),
    ("aggregator", "exact", "wallet account validation failed",
     "Wallet account validation failed", 25),
    ("aggregator", "exact", "voucher generation error",
     "Voucher generation failure", 26),
    ("bank", "starts_with", "transaction blocked for this branch",
     "Branch blocked by administrator", 27),
    ("aggregator", "contains", "amount must be greater than 10",
     "Minimum amount business rule rejection", 28),
    ("bank", "starts_with", "feature is not available for request bank",
     "Feature disabled for bank", 29),
    ("aggregator", "exact", "detail not found",
     "Account/detail not found", 30),
    ("aggregator", "exact", "merchant not found.",
     "Merchant not found at aggregator", 31),
    ("aggregator", "contains", "creditormerchpayload is required",
     "Aggregator payload validation error", 32),

    # --- Generic declines (checked last) ---
    ("aggregator", "exact", "failed", "Generic decline / unable to process", 90),
    ("aggregator", "exact", "unable to process", "Generic decline / unable to process", 91),
    ("aggregator", "exact", "unable to process/failed.", "Generic decline / unable to process", 92),
    ("aggregator", "exact", "the transaction could not be completed.",
     "Generic decline / unable to process", 93),
    ("aggregator", "exact", "transaction failed to complete.",
     "Generic decline / unable to process", 94),
]

# Real bank_wallet entities pulled directly from your actual settlement
# file's Acquirer Name column (these are 1:1 direct entities, not grouped
# under an aggregator). The ~30 individual cooperative codes from that same
# file are NOT seeded here -- those belong under whichever aggregator(s)
# you group them by, which you'll set up via the mapping page's JSON import.
# member_code, bucket, partner_name, institution_label
PARTNER_MAPPINGS = [
    ("007", "bank_wallet", "NIC ASIA Bank Ltd", "NIC ASIA Bank Ltd"),
    ("004", "bank_wallet", "Global IME Bank Ltd", "Global IME Bank Ltd"),
    ("002", "bank_wallet", "IME Khalti Ltd", "IME Khalti Ltd"),
    ("032", "bank_wallet", "CityPay", "CityPay"),
    ("020", "bank_wallet", "Cellcom Private Limited", "Cellcom Private Limited"),
    ("008", "bank_wallet", "MORU", "MORU"),
    ("030", "bank_wallet", "My Pay", "My Pay"),
    ("005", "bank_wallet", "Namaste Pay Ltd", "Namaste Pay Ltd"),
]


def run():
    with app.app_context():
        db.create_all()

        ClassificationRule.query.delete()
        PartnerMapping.query.delete()

        for side, match_type, pattern, category, priority in RULES:
            db.session.add(ClassificationRule(
                side=side, match_type=match_type, pattern=pattern,
                category=category, priority=priority, active=True,
            ))

        for member_code, bucket, partner_name, institution_label in PARTNER_MAPPINGS:
            db.session.add(PartnerMapping(
                member_code=member_code, bucket=bucket, partner_name=partner_name,
                institution_label=institution_label, active=True,
            ))

        db.session.commit()
        print(f"Seeded {len(RULES)} classification rules and {len(PARTNER_MAPPINGS)} partner mappings.")


if __name__ == "__main__":
    run()
