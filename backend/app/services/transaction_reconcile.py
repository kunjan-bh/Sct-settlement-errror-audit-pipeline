"""
Transaction-file reconciliation for the Settlement Type report.

The settlement file says "MID X settled NPR Y, Real Time". It does NOT say
which customer transaction that settlement is for. The switch's Transaction
List export does -- so when someone uploads BOTH files for the same day, the
per-entity MID sheets in the report can carry the originating transaction
alongside each settled MID, row-for-row.

Two export formats
------------------
The switch produces the transaction side in two shapes, and both are read
here (see _COLUMNS -- every field lists the names it can arrive under):

  * "Transaction List" -- title/metadata line, then a Title Case header:
    Transaction ID, Merchant ID, STAN, CRRN, Date & Time...
  * a raw dump -- header on the first row, snake_case: txn_id,
    merchant_code, crrn, txn_date_time... It has no STAN column, but does
    not need one (see below).

What is matchable, and what is not
----------------------------------
Measured on the real 13,926-row dump, the transaction side is two distinct
populations and only one of them is traceable from the settlement file:

  * issuer_network = SmartQR (6,651 rows): txn_id is the 6-char STAN and
    12-char CRRN concatenated -- 6,651 of 6,651, exactly. The settlement
    file stores that same STAN/CRRN split, so these trace exactly.
  * issuer_network = NQR (7,275 rows): txn_id is 19-char alphanumeric
    (2501250005224343QVY) and crrn is a bank reference
    (PRVUNPKA-1219580). The settlement file carries NEITHER -- searching
    every one of its columns for NPKA-/NPKT-/NQR- returns zero hits. There
    is no shared identifier, so these cannot be traced by key at all and
    are reported blank rather than guessed.

Tracing the link
----------------
Tiers, best first. All are exact-key: nothing is ever matched by
approximation, so an untraced settlement is reported blank rather than
guessed.

  0. Ref ID. The raw settlement dump carries `ref_id`, which IS the switch
     transaction id -- no reconstruction needed, and it is the only key that
     reaches the NQR population. Measured on the real 25 Aug 2026 pair,
     6,982 of 7,184 successful settlements (97.2%) trace this way, across
     both networks (NQR 4,689 + SmartQR 2,293). When the settlement file has
     this column, everything below is a fallback for the rows it misses.

The remaining tiers exist for the older Title Case export, which has no
ref_id at all:

  1. STAN + CRRN concatenated. The older settlement export splits the
     switch transaction id into a 6-char STAN and a 12-char CRRN (14,214 of
     14,214 rows in the sample file), and the transaction side carries the
     joined id:
         txn_id 823375861956400180 = STAN 823375 + CRRN 861956400180
     This is the primary key and covers the whole SmartQR population. The
     CRRN alone is a second probe -- it is unique across the dump (13,926
     of 13,926) -- which catches a row whose STAN was recorded differently.

  2. Remarks 1, on rows the settlement file did not settle cleanly (LO/in
     progress), holds "MID|TerminalID|TransactionID" --
     "294000000012524|29400012|2608250002844587TLE". The third field is the
     transaction id verbatim. NQR-issued transactions carry an alphanumeric
     STAN/CRRN in the Transaction List that the settlement file's numeric
     6+12 split cannot reproduce, so this is the only trace that reaches
     them.

The settlement file's "CR Transaction ID" is deliberately NOT used. It
looks like an issuer reference, but it is a different namespace entirely:
across the real 14,214-row file it holds ULIDs (01M0*, 4,897), bare digits
(4,199), zero-padded blanks (1,779), REQ_* (1,239) and UUIDs (875), while
the transaction side's issuer reference holds bank codes
(PCBLNPKA-1800496, MB-1374803). Intersecting the two real files on it
returns zero rows -- so matching on it could only ever fire by coincidence,
inventing a pairing. It is left alone.

A transaction is claimed by at most one settlement row (see _take): the same
MID settles repeatedly through the day, and letting two settlement rows point
at one transaction would invent a reconciliation that isn't there. Anything
untraced is reported as an empty row, never as a guess.
"""
import csv
import os

import pandas as pd

from app.services.retry_matching import parse_txn_datetime

TXN_ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# Any of these in a row means "this is the header row". Covers both export
# shapes; the raw dump puts its header on line 1 with no metadata above it.
_HEADER_MARKERS = ("Transaction ID", "txn_id", "s")

# (accepted source column names, key in the emitted dict, report header).
# Order is the order the columns appear in the report sheets. A field whose
# column is absent in a given export is simply blank rather than an error.
_COLUMNS = [
    (("Transaction ID", "txn_id", "s"), "txn_id", "Transaction ID"),
    (("Merchant ID", "merchant_code"), "txn_mid", "Txn MID"),
    (("Date & Time", "txn_date_time"), "txn_datetime", "Txn Date & Time"),
    (("Transaction Type", "txn_type"), "txn_type", "Txn Type"),
    (("Payment Mode", "txn_mode"), "payment_mode", "Txn Mode"),
    (("Transaction Amount", "txn_amount"), "txn_amount", "Txn Amount"),
    (("Network", "issuer_network"), "issuer_network", "Network"),
    (("Acquirer Status", "acquirer_status"), "acquirer_status", "Acquirer Status"),
    (("Issuer Name", "issuer_institution_name"), "issuer_name", "Issuer Name"),
    (("Issuer Status", "issuer_status"), "issuer_status", "Issuer Status"),
    (("Settlement Status", "settlement_status"), "txn_settlement_status", "Settlement Status"),
]

TXN_REPORT_HEADERS = [header for _, _, header in _COLUMNS] + ["Traced By"]
TXN_REPORT_KEYS = [key for _, key, _ in _COLUMNS] + ["traced_by"]

# Join columns, each with the names it can arrive under.
# "s" is what the raw transaction dump calls its transaction id column.
_TXN_ID_COLUMNS = ("Transaction ID", "txn_id", "s")
_CRRN_COLUMNS = ("CRRN", "crrn")
_STAN_COLUMNS = ("STAN",)  # the raw dump has none -- STAN+CRRN vs txn_id covers it
_DATETIME_COLUMNS = ("Date & Time", "txn_date_time")
_NETWORK_COLUMNS = ("Network", "issuer_network")
def _first_present(columns, candidates):
    """First of `candidates` that this export actually has, or None."""
    for name in candidates:
        if name in columns:
            return name
    return None

_TRACE_REF_ID = "Ref ID"
_TRACE_STAN_CRRN = "STAN+CRRN"
_TRACE_CRRN = "CRRN"
_TRACE_REMARKS = "Remarks trace"


def _read_raw(file_path: str, header, nrows=None) -> pd.DataFrame:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        # keep_default_na=False so a literal "-" or "NA" cell stays the text
        # the export wrote; dtype=str so ids never get numeric-ified (the
        # leading-zero problem that bit MID parsing in excel_ingest).
        return pd.read_csv(
            file_path, header=header, nrows=nrows, dtype=str, keep_default_na=False
        )
    return pd.read_excel(file_path, header=header, nrows=nrows, dtype=str)


def _find_txn_header_index(file_path: str, max_rows: int = 15) -> int:
    """0-based index of the row holding the real header. Mirrors
    excel_ingest._find_header_index, but keyed on 'Transaction ID' and
    reading whichever of csv/xlsx this file is.

    CSVs are scanned with the stdlib csv reader rather than pandas: the
    export's metadata line above the header has its own field count, and
    pandas' header=None read raises "Expected 3 fields, saw 15" on exactly
    that shape. csv.reader takes ragged rows as they come. Reading the data
    itself is safe either way, since by then the header row fixes the width
    and the short rows above it are skipped.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        with open(file_path, "r", encoding="utf-8-sig", newline="") as fh:
            for idx, fields in enumerate(csv.reader(fh)):
                if idx >= max_rows:
                    break
                if any(f.strip() in _HEADER_MARKERS for f in fields):
                    return idx
    else:
        preview = _read_raw(file_path, header=None, nrows=max_rows)
        for idx, row in preview.iterrows():
            for val in row.values:
                if pd.notna(val) and str(val).strip() in _HEADER_MARKERS:
                    return int(idx)

    markers = " or ".join(f"'{m}'" for m in _HEADER_MARKERS)
    raise ValueError(
        f"Could not find a header row containing {markers} within the first "
        f"{max_rows} rows. Is this a transaction export?"
    )


def read_transaction_dataframe(file_path: str) -> pd.DataFrame:
    """Transaction List export (.csv/.xlsx/.xls) -> DataFrame with the real
    header applied and every value left as text."""
    header_idx = _find_txn_header_index(file_path)
    df = _read_raw(file_path, header=header_idx)
    df.columns = [str(c).strip() if pd.notna(c) else c for c in df.columns]
    df = df.dropna(how="all")

    if _first_present(set(df.columns), _TXN_ID_COLUMNS) is None:
        markers = " or ".join(f"'{m}'" for m in _TXN_ID_COLUMNS)
        raise ValueError(
            f"Transaction file has no {markers} column; found: {list(df.columns)}"
        )
    return df


def _text(value) -> str:
    """Cell -> trimmed text, with the export's placeholders ('-', 'nan',
    blank) all collapsing to '' so they never become lookup keys."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if text in ("-", "nan", "NaN", "None"):
        return ""
    return text


# The settlement export's canonical widths for the two halves of the switch
# transaction id. The settlement file reads these as int64 (they are bare
# digits), so a value that legitimately starts with a zero comes back one
# digit short -- the same leading-zero loss that dtype={"MID": str} guards
# against in excel_ingest. Re-padding before lookup keeps such a row matchable
# against the Transaction List, which is read as text throughout.
_STAN_WIDTH = 6
_CRRN_WIDTH = 12


def _pad(value: str, width: int) -> str:
    return value.zfill(width) if value.isdigit() and len(value) < width else value


def remarks_trace_id(remark) -> str:
    """Third field of a 'MID|TerminalID|TransactionID' Remarks 1 value, or ''
    for the ordinary free-text remarks ('Success', API error dumps)."""
    text = _text(remark)
    if "|" not in text:
        return ""
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 3 or not parts[2]:
        return ""
    return parts[2]


class TransactionIndex:
    """
    Lookup from settlement-row trace keys to Transaction List rows, with each
    transaction claimable exactly once.

    Built once per report; `match()` is called per settled MID row, so the
    per-row cost is a handful of dict hits regardless of file size.
    """

    def __init__(self, df: pd.DataFrame, file_name: str = ""):
        self.file_name = file_name
        self.row_count = len(df)
        # The window this file covers, kept so the report can say WHY nothing
        # traced. A settlement run covers a cycle (e.g. 24 Aug 10:00 -> 25 Aug
        # 09:59), so a transaction export for "the 25th" starting at midnight
        # only overlaps part of it -- the single most likely reason for a zero
        # trace rate, and invisible without this.
        self.window_from = None
        self.window_to = None
        self._rows: list[dict] = []
        self._by_txn_id: dict[str, list[int]] = {}
        self._by_crrn: dict[str, list[int]] = {}
        self._by_stan_crrn: dict[tuple, list[int]] = {}
        self._claimed: set[int] = set()
        # issuer_network -> row count, reported so the share of the file that
        # is structurally untraceable (NQR) is visible rather than inferred.
        self.by_network: dict[str, int] = {}

        columns = set(df.columns)
        # Resolve each field once, here, rather than probing per row.
        present_fields = [
            (source, key)
            for names, key, _ in _COLUMNS
            if (source := _first_present(columns, names)) is not None
        ]
        txn_id_col = _first_present(columns, _TXN_ID_COLUMNS)
        crrn_col = _first_present(columns, _CRRN_COLUMNS)
        stan_col = _first_present(columns, _STAN_COLUMNS)
        when_col = _first_present(columns, _DATETIME_COLUMNS)
        network_col = _first_present(columns, _NETWORK_COLUMNS)

        for pos, (_, row) in enumerate(df.iterrows()):
            self._rows.append({key: _text(row.get(source)) for source, key in present_fields})

            txn_id = _text(row.get(txn_id_col)) if txn_id_col else ""
            if txn_id:
                self._by_txn_id.setdefault(txn_id, []).append(pos)

            crrn = _pad(_text(row.get(crrn_col)), _CRRN_WIDTH) if crrn_col else ""
            if crrn:
                self._by_crrn.setdefault(crrn, []).append(pos)

            if stan_col and crrn:
                stan = _pad(_text(row.get(stan_col)), _STAN_WIDTH)
                if stan:
                    self._by_stan_crrn.setdefault((stan, crrn), []).append(pos)

            if when_col:
                when = parse_txn_datetime(_text(row.get(when_col)))
                if when is not None:
                    if self.window_from is None or when < self.window_from:
                        self.window_from = when
                    if self.window_to is None or when > self.window_to:
                        self.window_to = when

            if network_col:
                net = _text(row.get(network_col)) or "unknown"
                self.by_network[net] = self.by_network.get(net, 0) + 1


    @property
    def matched_count(self) -> int:
        return len(self._claimed)

    def _take(self, positions):
        """First position not already claimed by an earlier settlement row."""
        if not positions:
            return None
        for pos in positions:
            if pos not in self._claimed:
                self._claimed.add(pos)
                return pos
        return None

    def match(self, *, ref_id=None, stan=None, crrn=None, remark=None):
        """
        Trace one settled MID row to its transaction, tiers in the order
        documented at the top of this module. Returns the transaction's
        report fields plus `traced_by`, or None when nothing matches -- the
        caller renders that as a blank row.
        """
        ref_text = _text(ref_id)
        if ref_text:
            pos = self._take(self._by_txn_id.get(ref_text))
            if pos is not None:
                return {**self._rows[pos], "traced_by": _TRACE_REF_ID}

        stan_text = _pad(_text(stan), _STAN_WIDTH)
        crrn_text = _pad(_text(crrn), _CRRN_WIDTH)

        if stan_text and crrn_text:
            joined = f"{stan_text}{crrn_text}"
            pos = self._take(self._by_txn_id.get(joined))
            if pos is None:
                pos = self._take(self._by_stan_crrn.get((stan_text, crrn_text)))
            if pos is not None:
                return {**self._rows[pos], "traced_by": _TRACE_STAN_CRRN}

        # CRRN alone -- unique across the dump (13,926 of 13,926), so it is a
        # safe second probe for a row whose STAN was recorded differently.
        if crrn_text:
            pos = self._take(self._by_crrn.get(crrn_text))
            if pos is not None:
                return {**self._rows[pos], "traced_by": _TRACE_CRRN}

        trace_id = remarks_trace_id(remark)
        if trace_id:
            pos = self._take(self._by_txn_id.get(trace_id))
            if pos is not None:
                return {**self._rows[pos], "traced_by": _TRACE_REMARKS}

        return None


def build_transaction_index(file_path: str, file_name: str = "") -> TransactionIndex:
    return TransactionIndex(read_transaction_dataframe(file_path), file_name)


def attach_transactions(mid_rows: list[dict], index) -> dict:
    """
    Fills each mid_row's "txn" with its traced transaction (or None) and
    returns the reconciliation tally for the report's Summary sheet. Returns
    {} when no transaction file was uploaded -- the report then renders
    exactly as it did before this feature existed.

    Ordering matters: mid_rows arrive sorted by entity/MID, and claims are
    first-come, so the same input pair always produces the same pairing.
    """
    if index is None:
        for row in mid_rows:
            row["txn"] = None
        return {}

    matched = 0
    for row in mid_rows:
        txn = index.match(
            ref_id=row.get("ref_id"),
            stan=row.get("stan"),
            crrn=row.get("crrn"),
            remark=row.get("remark"),
        )
        row["txn"] = txn
        if txn:
            matched += 1

    return {
        "file_name": index.file_name,
        "txn_rows": index.row_count,
        "settled_rows": len(mid_rows),
        "matched": matched,
        "unmatched": len(mid_rows) - matched,
        "txn_window": _window_text(index.window_from, index.window_to),
        "by_network": dict(index.by_network),
    }


def _window_text(start, end) -> str:
    if start is None or end is None:
        return ""
    fmt = "%d %b %Y %I:%M %p"
    return f"{start.strftime(fmt)} to {end.strftime(fmt)}"
