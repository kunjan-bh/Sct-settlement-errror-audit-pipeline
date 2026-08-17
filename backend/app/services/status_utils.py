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
