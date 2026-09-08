from datetime import datetime
from app.extensions import db


class Batch(db.Model):
    """
    A Batch = one uploaded settlement Excel + everything derived from it.

    Design decision: we store `status` as open/finished rather than deleting
    or archiving batches. "Finish Batch" just flips this flag and generates
    the report -- it does NOT lock editing. This matters because in real ops
    work you sometimes need to reopen a "finished" batch next week when an
    aggregator disputes a solved issue. We are not implementing that reopen
    flow yet, but choosing a flag over a delete/archive means it's a
    two-line change later instead of a schema migration.
    """

    __tablename__ = "batches"

    id = db.Column(db.Integer, primary_key=True)

    # e.g. "Batch_2026_07_19". If a second batch is uploaded same day,
    # service layer appends _2, _3 etc. Kept unique so URLs/search are stable.
    name = db.Column(db.String(64), unique=True, nullable=False, index=True)

    status = db.Column(db.String(16), nullable=False, default="open")

    # "upload"  -- a settlement Excel was ingested; transactions hang off it.
    # "session" -- an office session. No spreadsheet: the batch is opened when
    #              someone sits down to work, collects the dispute decisions
    #              they make while it is open, and is closed at the end of the
    #              day to produce the report.
    # Both kinds share this table so the report, notes and email flow work the
    # same either way, and so the nine existing upload batches keep working.
    kind = db.Column(db.String(16), nullable=False, default="upload", index=True)



    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)

    # Paths on disk (not blobs in DB -- keeps DB small and lets us stream
    # large excel files instead of loading them through SQLAlchemy).
    input_file_path = db.Column(db.String(512), nullable=True)
    output_file_path = db.Column(db.String(512), nullable=True)

    notes = db.Column(db.Text, nullable=True)  # the "General Notes" textarea

    # Relationships. cascade="all, delete-orphan" means deleting a Batch
    # cleans up its transactions/issue statuses too -- we never want orphan
    # transaction rows pointing at a batch_id that no longer exists.
    transactions = db.relationship(
        "Transaction", backref="batch", cascade="all, delete-orphan", lazy="dynamic"
    )
    issue_statuses = db.relationship(
        "IssueStatus", backref="batch", cascade="all, delete-orphan", lazy="dynamic"
    )
    # Dispute decisions made while this session was open. No cascade delete:
    # a decision about a live settlement outlives the session it was taken in,
    # and losing "we already checked this one" would mean chasing it twice.
    dispute_statuses = db.relationship("DisputeStatus", backref="batch", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "kind": self.kind,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "notes": self.notes,
        }
