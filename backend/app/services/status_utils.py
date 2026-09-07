"""
One place that decides what an input file's `Status` cell means.

Why this exists: ingest, the dashboard, and the report generator all have to
turn the raw Status string into the same bucket key, because that key is part
of the IssueStatus identity (batch, side, partner, category, txn_status). If
two of them slugify differently, the report silently looks up issues that
don't exist and every row falls back to "pending".

The bug this was written for: the real settlement files spell the third
bucket "In progress", which naively slugifies to "in_progress" -- but every
bucket in the codebase is named "lo_progress" (ops calls it "LO"). So those
rows matched nothing, never got an issue row, and never showed on the
dashboard. Spelling variants are aliased here instead of at each call site.
"""

# raw (lowercased, whitespace-collapsed) -> bucket key
_ALIASES = {
    "in progress": "lo_progress",
    "in-progress": "lo_progress",
    "inprogress": "lo_progress",
    "in_progress": "lo_progress",
    "lo progress": "lo_progress",
    "lo-progress": "lo_progress",
    "lo_progress": "lo_progress",
    "lo": "lo_progress",
    "success": "success",
    "successful": "success",
    "failed": "failed",
    "failure": "failed",
    "fail": "failed",
    "pending": "pending",
}

# Buckets the dashboard/report show as actionable, in display order.
ISSUE_BUCKETS = ("failed", "pending", "lo_progress")


def normalize_txn_status(raw) -> str:
    """
    'In progress' -> 'lo_progress', 'Failed' -> 'failed', None -> 'unknown'.
    Anything unrecognized is slugified (spaces -> underscores) so a new
    status value in a future file still groups consistently instead of
    crashing or collapsing into an existing bucket.
    """
    if raw is None:
        return "unknown"
    text = " ".join(str(raw).strip().lower().split())
    if not text:
        return "unknown"
    if text in _ALIASES:
        return _ALIASES[text]
    return text.replace(" ", "_")


def is_error_status(raw) -> bool:
    """True for rows that represent something ops has to look at."""
    return normalize_txn_status(raw) != "success"


# Partner types that get their own card in the dashboard. Anything else --
# an unmapped MID, a bare "No Aggregator" -- falls to the SCT summary.
PARTNER_CARD_TYPES = ("aggregator", "bank_wallet")


def issue_partner_key(partner_name, partner_type):
    """
    The `partner_name` half of an IssueStatus identity, for a transaction.

    This is deliberately keyed on partner_type rather than error_side. Partner
    cards took over every failure for their partner regardless of whose fault
    it was, because ops chases an Interpay timeout with the aggregator anyway.
    The old rule -- "no partner if the side is sct" -- predates that, and filed
    the operator's decisions under ('sct', None, ...) while the dashboard read
    and wrote ('sct', 'Global IME Bank Ltd', ...). Those two keys drift apart
    the moment anyone marks something solved, and the mail and the report then
    quietly report a batch as unfinished that ops had finished.

    Must stay in step with _build_partner_summary / _build_sct_summary in
    dashboard_service: the dashboard is what the operator actually clicks, so
    it defines the identity and everything else follows it.
    """
    return partner_name if partner_type in PARTNER_CARD_TYPES else None
