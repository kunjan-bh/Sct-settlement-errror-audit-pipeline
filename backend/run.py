"""
Run with: python run.py
Starts the Flask dev server on http://localhost:5000
"""
from app import create_app
from app.extensions import db
from app.schema_upgrade import ensure_schema
from app.services.retry_matching import reconcile_retries

app = create_app()

with app.app_context():
    db.create_all()  # safe to call repeatedly -- only creates missing tables

    # create_all cannot add columns to tables that already exist, so new
    # Transaction columns are applied (and backfilled from extra_data) here.
    upgrade = ensure_schema()
    if upgrade["added"]:
        print(f"Schema upgrade: added {upgrade['added']}, backfilled {upgrade['backfilled']} rows")
        # Existing batches have never been retry-matched; do it once now.
        stats = reconcile_retries(all_time=True)
        print(f"Retry reconciliation: {stats['resolved']} failures already settled by a reprocess")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
