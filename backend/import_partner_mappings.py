"""
Import partner mappings from the Smart QR Member BIN export.

    python import_partner_mappings.py "path/to/Smart QR Member BIN - Bin List.csv"          # dry run
    python import_partner_mappings.py "path/to/..." --apply                                  # write

Why this exists: PartnerResolver turns the first three characters of a MID
into a partner via the partner_mappings table (services/classification_service
.py). Without a row, a MID resolves to "No Aggregator", so every code the
switch issues has to be present or whole aggregators vanish from the
dashboard.

The BIN export is the source of truth for that list, but its aggregator column
is free text -- "ismart devanasoft", "InfoDevelopers", "infodevelopers",
"mbank", "Mbank" all appear -- so labels are normalised to the canonical names
the rest of the app already uses (_CANONICAL below). Getting this wrong does
not error; it silently splits one aggregator into two on every report.

Dry run by default. Nothing is written without --apply.
"""
import argparse
import csv
import sys
from collections import Counter, defaultdict

from app import create_app
from app.extensions import db
from app.models.partner_mapping import PartnerMapping

# Column positions in the export. The header row is mislabelled -- "ach" sits
# over the institution name and two columns are blank -- so these are indexes
# rather than header lookups, verified against the real file.
COL_MEMBER_CODE = 2
COL_AGGREGATOR = 3
COL_INSTITUTION = 4

# Free-text label in the file -> the name used everywhere else in the app.
# Keys are lowercased before lookup, so only genuinely different spellings
# need an entry here.
_CANONICAL = {
    "ismart devanasoft": ("Ismart", "aggregator"),
    "ismart": ("Ismart", "aggregator"),
    "infodevelopers": ("InfoDevelopers", "aggregator"),
    "mbank": ("Mbank", "aggregator"),
    "mofin": ("Mofin", "aggregator"),
    "cosys": ("Cosys", "aggregator"),
    "microbank": ("Microbank", "aggregator"),
    "myratech": ("Myratech", "aggregator"),
    "planetearth": ("Planetearth", "aggregator"),
    "prathamit": ("PrathamIT", "aggregator"),
    # Not an aggregator: the member is the bank itself, so it belongs in the
    # bank_wallet bucket alongside the directly-connected banks and wallets.
    "bank": (None, "bank_wallet"),
}


def normalise(label: str, institution: str):
    """(partner_name, bucket) for one row, or (None, None) to skip it."""
    key = (label or "").strip().lower()
    if not key:
        return None, None
    if key not in _CANONICAL:
        return None, None
    name, bucket = _CANONICAL[key]
    # "Bank" names the bucket, not the partner -- the institution column has
    # the actual bank name.
    return (name or (institution or "").strip() or None), bucket


def read_rows(path: str):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[1:]  # drop the header


def build(path: str):
    """CSV -> {member_code: (partner_name, bucket, institution)} plus the
    things a human should look at before this is written."""
    wanted = {}
    conflicts = defaultdict(list)
    unknown_labels = Counter()
    skipped_no_code = skipped_no_agg = 0

    for row in read_rows(path):
        if len(row) <= COL_INSTITUTION:
            continue
        code = (row[COL_MEMBER_CODE] or "").strip()
        label = (row[COL_AGGREGATOR] or "").strip()
        institution = (row[COL_INSTITUTION] or "").strip()

        if not code:
            skipped_no_code += 1
            continue
        if not label:
            skipped_no_agg += 1
            continue

        name, bucket = normalise(label, institution)
        if not name:
            unknown_labels[label] += 1
            continue

        # MIDs are matched on their first three characters, and the export
        # writes short codes unpadded ("3" for "003").
        code = code.zfill(3)

        if code in wanted and wanted[code][:2] != (name, bucket):
            conflicts[code].append((name, bucket, institution))
            continue
        wanted[code] = (name, bucket, institution)

    return wanted, conflicts, unknown_labels, skipped_no_code, skipped_no_agg


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path")
    ap.add_argument("--apply", action="store_true", help="write to the database")
    args = ap.parse_args()

    wanted, conflicts, unknown, no_code, no_agg = build(args.csv_path)

    app = create_app()
    with app.app_context():
        existing = {m.member_code: m for m in PartnerMapping.query.all()}

        to_add, to_update, unchanged = [], [], 0
        for code, (name, bucket, institution) in sorted(wanted.items()):
            row = existing.get(code)
            if row is None:
                to_add.append((code, name, bucket, institution))
            elif (row.partner_name, row.bucket) != (name, bucket):
                to_update.append((code, row.partner_name, row.bucket, name, bucket))
            else:
                unchanged += 1

        # Rows already in the table that the export says nothing about. These
        # are left alone: the directly-connected banks and wallets were entered
        # by hand and are not in the aggregator export.
        untouched = sorted(set(existing) - set(wanted))

        print(f"parsed {len(wanted)} member codes from {args.csv_path}")
        print(f"  skipped: {no_code} without a member code, {no_agg} without an aggregator")
        if unknown:
            print(f"  UNRECOGNISED aggregator labels (skipped, add to _CANONICAL):")
            for label, n in unknown.most_common():
                print(f"     {label!r} x{n}")
        if conflicts:
            print(f"  CONFLICTS -- one code, two different partners:")
            for code, others in conflicts.items():
                kept = wanted.get(code)
                print(f"     {code}: kept {kept[0]!r} ({kept[2][:34]!r})")
                for name, _b, inst in others:
                    print(f"          dropped {name!r} ({inst[:34]!r})")
        print()
        print(f"  to add     : {len(to_add)}")
        print(f"  to update  : {len(to_update)}")
        print(f"  unchanged  : {unchanged}")
        print(f"  left alone : {len(untouched)}  {untouched}")

        by_partner = Counter(v[0] for v in wanted.values())
        print()
        print("  codes per partner:")
        for name, n in by_partner.most_common():
            print(f"     {name:26} {n}")

        for code, was_name, was_bucket, name, bucket in to_update:
            print(f"  UPDATE {code}: {was_name!r}/{was_bucket} -> {name!r}/{bucket}")

        if not args.apply:
            print()
            print("DRY RUN -- nothing written. Re-run with --apply to save.")
            return

        for code, name, bucket, institution in to_add:
            db.session.add(PartnerMapping(
                member_code=code, partner_name=name, bucket=bucket,
                institution_label=institution or None, active=True,
            ))
        for code, _wn, _wb, name, bucket in to_update:
            row = existing[code]
            row.partner_name, row.bucket = name, bucket
        db.session.commit()

        print()
        print(f"WROTE {len(to_add)} new and {len(to_update)} updated mappings.")
        print(f"partner_mappings now holds {PartnerMapping.query.count()} rows.")


if __name__ == "__main__":
    sys.exit(main())
