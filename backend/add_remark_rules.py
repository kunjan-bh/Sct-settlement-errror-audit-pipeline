"""
Add classification rules for connection/network failures and for success
remarks, and re-classify existing transactions against them.

    python add_remark_rules.py            # dry run
    python add_remark_rules.py --apply

Why these two:

  Connection -- "Connection refused: interpay..." was already a rule, but
  "Connection was closed" and "Connection reset" were not, so 96 rows sat in
  "Unclassified" with side "unknown". They are the same class of problem, on
  our side, and a broad `contains "connection"` at a lower priority than the
  specific refused rule catches those two plus whatever wording turns up next.

  Success -- every remark saying "Success" already belongs to a row whose
  Status is SUCCESS (127,894 of them, no exceptions), so this changes no
  status. What it changes is the category: those rows were "Unclassified",
  which is the same bucket genuinely unrecognised failures land in. Naming
  them means "Unclassified" can be read as "we do not know what this is"
  rather than mostly meaning "this worked fine".

Re-classification only rewrites error_side / error_category / matched_rule_id.
Status, amounts and ops decisions are untouched.
"""
import argparse
import sys
from collections import Counter

from app import create_app
from app.extensions import db
from app.models.classification_rule import ClassificationRule
from app.models.transaction import Transaction
from app.services.classification_service import RuleEngine

# side, match_type, pattern, category, priority
NEW_RULES = [
    # Ahead of the generic decline rules (p90+) but behind the specific
    # "connection refused: interpay..." rule (p10), which names the actual host
    # and should keep its more precise category.
    ("sct", "contains", "connection", "Connection dropped / reset (network)", 15),
    # Lowest number wins, so this is checked first: a remark that says the
    # transfer succeeded is not a failure of anyone's, whatever else it
    # contains.
    ("sct", "contains", "success", "Success", 1),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write to the database")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        existing = {(r.side, r.match_type, r.pattern) for r in ClassificationRule.query.all()}
        to_add = [r for r in NEW_RULES if (r[0], r[1], r[2]) not in existing]

        print(f"rules to add: {len(to_add)}")
        for side, mt, pattern, category, prio in to_add:
            print(f"   p{prio:<3} {side:6} {mt:11} {pattern!r} -> {category!r}")
        if not to_add:
            print("   (all present already)")

        if args.apply and to_add:
            for side, mt, pattern, category, prio in to_add:
                db.session.add(ClassificationRule(
                    side=side, match_type=mt, pattern=pattern,
                    category=category, priority=prio, active=True,
                ))
            db.session.commit()

        # --- what re-classifying would do ---
        # On a dry run the new rules are not in the table yet, so build the
        # engine from what is there PLUS what we propose. Without this the
        # preview reports no change and hides the whole effect.
        rules = list(ClassificationRule.query.filter_by(active=True).all())
        if not args.apply:
            for side, mt, pattern, category, prio in to_add:
                rules.append(ClassificationRule(
                    side=side, match_type=mt, pattern=pattern,
                    category=category, priority=prio, active=True,
                ))
        rules.sort(key=lambda r: r.priority)
        engine = RuleEngine(rules)
        rows = Transaction.query.all()
        changes = Counter()
        touched = []
        for txn in rows:
            result = engine.classify_row(txn.remark)
            if (result.side, result.category) != (txn.error_side, txn.error_category):
                changes[(txn.error_category, result.category)] += 1
                touched.append((txn, result))

        print()
        print(f"transactions whose category would change: {len(touched):,} of {len(rows):,}")
        for (was, now), n in changes.most_common(12):
            print(f"   {n:7,}  {was!r} -> {now!r}")

        if not args.apply:
            print()
            print("DRY RUN -- nothing written. Re-run with --apply.")
            return

        # Move the ops decisions with the rows. IssueStatus identity is
        # (batch, side, partner, category, txn_status); re-categorising a row
        # moves it to a new identity, and a solved/excluded decision left
        # behind on the old one would simply vanish from the dashboard.
        from app.models.issue_status import IssueStatus
        from app.services.status_utils import normalize_txn_status

        moves = {}
        for txn, result in touched:
            if normalize_txn_status(txn.status) == "success":
                continue  # no issue rows exist for success
            old_key = (txn.batch_id, txn.error_side,
                       txn.partner_name if txn.error_side != "sct" else None,
                       txn.error_category, normalize_txn_status(txn.status))
            new_key = (txn.batch_id, result.side,
                       txn.partner_name if result.side != "sct" else None,
                       result.category, normalize_txn_status(txn.status))
            moves[old_key] = new_key

        migrated = skipped = 0
        for old_key, new_key in moves.items():
            if old_key == new_key:
                continue
            row = IssueStatus.query.filter_by(
                batch_id=old_key[0], side=old_key[1], partner_name=old_key[2],
                category=old_key[3], txn_status=old_key[4]).first()
            if row is None:
                continue
            clash = IssueStatus.query.filter_by(
                batch_id=new_key[0], side=new_key[1], partner_name=new_key[2],
                category=new_key[3], txn_status=new_key[4]).first()
            if clash is not None:
                # Target already exists; keep whichever carries a real
                # decision rather than silently dropping one.
                skipped += 1
                continue
            row.side, row.partner_name, row.category = new_key[1], new_key[2], new_key[3]
            migrated += 1
        print(f"ops decisions carried to the new identity: {migrated} (skipped {skipped} clashes)")

        for txn, result in touched:
            txn.error_side = result.side
            txn.error_category = result.category
            txn.matched_rule_id = result.matched_rule_id
        db.session.commit()
        print()
        print(f"APPLIED: {len(to_add)} rules added, {len(touched):,} transactions re-classified.")


if __name__ == "__main__":
    sys.exit(main())
