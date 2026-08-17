"""
Tiny forward-only schema upgrader.

Why this exists: the app creates tables with `db.create_all()`, which only
ever CREATEs missing tables -- it will not add a column to a table that
already exists. There is no Alembic here, and the running instance already
holds real batches, so shipping new Transaction columns needs an explicit
(idempotent) ALTER pass.

Scope is deliberately small: add missing columns, add their indexes, and
backfill the new columns from `extra_data` the one time they appear. Anything
more complicated than that is a sign it's time to adopt Alembic properly.
"""
from sqlalchemy import inspect, text

from app.extensions import db

# column name -> DDL type, for columns added after the initial release
_TRANSACTION_COLUMNS = {
    "txn_amount": "NUMERIC(18, 2)",
    "settled_by": "VARCHAR(32)",
    "beneficiary_id": "VARCHAR(64)",
    "txn_datetime": "DATETIME",
    "retry_resolved": "BOOLEAN DEFAULT 0 NOT NULL",
    "retry_resolved_by_id": "INTEGER",
}

_TRANSACTION_INDEXES = {
    "ix_transactions_txn_amount": "txn_amount",
    "ix_transactions_beneficiary_id": "beneficiary_id",
    "ix_transactions_txn_datetime": "txn_datetime",
    "ix_transactions_retry_resolved": "retry_resolved",
}

# extra_data key -> (column, parser); the source columns these were promoted from
_BACKFILL_SOURCES = {
    "Txn Amount": "txn_amount",
    "Settled By": "settled_by",
    "Bank Account/Wallet ID": "beneficiary_id",
    "Transaction Date": "txn_datetime",
}


def ensure_schema() -> dict:
    """
    Bring the live database up to the current model. Safe to call on every
    startup: it inspects first and does nothing when already current.
    """
    inspector = inspect(db.engine)
    if "transactions" not in inspector.get_table_names():
        return {"added": [], "backfilled": 0}  # fresh DB; create_all handles it

    existing = {c["name"] for c in inspector.get_columns("transactions")}
    added = [name for name in _TRANSACTION_COLUMNS if name not in existing]

    with db.engine.begin() as conn:
        for name in added:
            conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {name} {_TRANSACTION_COLUMNS[name]}"))

        existing_indexes = {i["name"] for i in inspector.get_indexes("transactions")}
        for index_name, column in _TRANSACTION_INDEXES.items():
            if index_name not in existing_indexes and column in (existing | set(added)):
                conn.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {index_name} ON transactions ({column})")
                )

    backfilled = _backfill_promoted_columns() if added else 0
    return {"added": added, "backfilled": backfilled}


def _backfill_promoted_columns() -> int:
    """
    Populate the newly added columns for rows ingested before they existed.
    The values were never lost -- they were sitting in extra_data all along.
    """
    # Imported here: this module is loaded during app setup, before services.
    from app.models.transaction import Transaction
    from app.services.retry_matching import parse_amount, parse_txn_datetime

    rows = Transaction.query.filter(Transaction.extra_data.isnot(None)).all()
    updated = 0

    for txn in rows:
        extra = txn.extra_data or {}
        if not any(key in extra for key in _BACKFILL_SOURCES):
            continue

        txn.txn_amount = parse_amount(extra.get("Txn Amount"))
        txn.settled_by = (str(extra["Settled By"]).strip() if extra.get("Settled By") else None)
        beneficiary = extra.get("Bank Account/Wallet ID")
        txn.beneficiary_id = str(beneficiary).strip() if beneficiary not in (None, "") else None
        txn.txn_datetime = parse_txn_datetime(extra.get("Transaction Date"))
        updated += 1

    if updated:
        db.session.commit()
    return updated
