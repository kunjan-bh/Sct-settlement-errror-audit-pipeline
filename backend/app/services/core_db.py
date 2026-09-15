"""
Read-only access to the live GetPay/SCT switch database.

This is production. Nothing in this application has any business writing to
it, so "read only" is enforced three times over, at decreasing levels of
trust:

  1. Postgres itself. Every connection is opened with
     `default_transaction_read_only=on`, so the server rejects any INSERT,
     UPDATE, DELETE, TRUNCATE or DDL with a 25006 error regardless of what
     this process asks for. This is the guarantee that actually matters: it
     holds even if the guard below is wrong, bypassed, or edited away.
  2. A statement guard (`assert_read_only`) that refuses anything that is not
     a single SELECT/WITH -- which also stops a second statement being
     smuggled in after a semicolon.
  3. `autocommit=False` with an unconditional rollback, so nothing is left
     open against the switch.

The right long-term answer is a database role with no write grants. Ask the
DBA for one; until then the above is what the client can enforce on its own.

Connection settings come from the environment (CORE_DB_*, in backend/.env),
never from the database or the UI -- the switch credentials are not the
app's to hand out.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

# Statements that would change something. Checked as whole words against SQL
# with its comments and string literals removed, so a merchant named
# "DELETE ME" in a WHERE clause does not trip the guard.
_FORBIDDEN = (
    "insert", "update", "delete", "truncate", "drop", "create", "alter",
    "grant", "revoke", "comment", "merge", "upsert", "copy", "vacuum",
    "reindex", "cluster", "refresh", "call", "do", "execute", "prepare",
    "listen", "notify", "lock", "set", "reset", "begin", "commit",
    "rollback", "savepoint", "security",
)

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_SINGLE_QUOTED = re.compile(r"'(?:[^']|'')*'", re.S)
_DOUBLE_QUOTED = re.compile(r'"(?:[^"]|"")*"', re.S)
_DOLLAR_QUOTED = re.compile(r"\$(\w*)\$.*?\$\1\$", re.S)


class ReadOnlyViolation(Exception):
    """Raised before anything is sent, when a statement is not a plain read."""


def _scrub(sql: str) -> str:
    """SQL with comments and quoted literals blanked out, for keyword checks."""
    sql = _COMMENT_BLOCK.sub(" ", sql)
    sql = _COMMENT_LINE.sub(" ", sql)
    sql = _DOLLAR_QUOTED.sub(" '' ", sql)
    sql = _SINGLE_QUOTED.sub(" '' ", sql)
    sql = _DOUBLE_QUOTED.sub(" x ", sql)
    return sql


def assert_read_only(sql: str) -> None:
    """
    Raise ReadOnlyViolation unless `sql` is a single SELECT or WITH.

    Deliberately strict rather than clever: an allowlist of two openers and a
    ban on multiple statements is easy to reason about, and the cost of a
    false positive here is only that someone has to phrase a query
    differently. The cost of a false negative is a write to the live switch.
    """
    if not sql or not sql.strip():
        raise ReadOnlyViolation("Empty statement.")

    scrubbed = _scrub(sql).strip()

    # One statement only. A trailing semicolon is fine; anything after it is
    # a second statement and is refused.
    if ";" in scrubbed.rstrip().rstrip(";"):
        raise ReadOnlyViolation(
            "Multiple statements are not allowed -- send one SELECT at a time."
        )

    first = re.match(r"\(*\s*([a-z_]+)", scrubbed, re.I)
    opener = (first.group(1) if first else "").lower()
    if opener not in ("select", "with", "table", "values"):
        raise ReadOnlyViolation(
            f"Only SELECT queries are allowed against the switch (got '{opener or sql.strip()[:20]}')."
        )

    words = set(re.findall(r"[a-z_]+", scrubbed.lower()))
    hit = sorted(words & set(_FORBIDDEN))
    if hit:
        # `with ... as (insert ... returning)` is real, valid, writing SQL, so
        # the keyword ban applies to CTEs too rather than only to the opener.
        raise ReadOnlyViolation(
            f"Statement contains write keyword(s): {', '.join(hit)}."
        )


LIVE = "live"
UAT = "uat"
ENVIRONMENTS = (LIVE, UAT)

# Which switch every read goes to. Held in the database rather than per
# browser, because the server does the querying: if it were a browser setting,
# one tab on UAT would silently change what another tab on live was reading.
_ENV_SETTING_KEY = "core_db_environment"


def active_environment() -> str:
    """
    The switch currently selected. Live unless someone chose otherwise, and
    live again if the stored value is anything unexpected -- defaulting to
    production is the safe direction for a read-only connection, and a typo
    should not quietly point reports at test data.
    """
    try:
        from app.models.app_setting import AppSetting

        row = AppSetting.query.filter_by(key=_ENV_SETTING_KEY).first()
        value = (row.value if row else "") or LIVE
    except Exception:  # noqa: BLE001 - no app context, or the table is not there yet
        value = LIVE
    return value if value in ENVIRONMENTS else LIVE


def set_active_environment(name: str) -> str:
    """Switch which database everything reads from. Refuses a name it does not
    know rather than falling through to a default."""
    if name not in ENVIRONMENTS:
        raise ValueError(f"Unknown environment {name!r}; expected one of {', '.join(ENVIRONMENTS)}.")

    from app.extensions import db
    from app.models.app_setting import AppSetting

    row = AppSetting.query.filter_by(key=_ENV_SETTING_KEY).first()
    if row is None:
        row = AppSetting(key=_ENV_SETTING_KEY, value=name)
        db.session.add(row)
    else:
        row.value = name
    db.session.commit()
    return name


def _env_prefix(name: str) -> str:
    return "CORE_DB_" if name == LIVE else "CORE_DB_UAT_"


def core_db_config(environment: str | None = None) -> dict:
    """
    Connection settings for the selected switch. Password included -- callers
    must not log or return this.

    Timeouts and the row cap are deliberately shared: they are about protecting
    a database from this application, and that applies to UAT too.
    """
    name = environment or active_environment()
    p = _env_prefix(name)
    return {
        "environment": name,
        "host": os.getenv(f"{p}HOST", "").strip(),
        "port": int(os.getenv(f"{p}PORT", "5432") or 5432),
        "dbname": os.getenv(f"{p}NAME", "").strip(),
        "user": os.getenv(f"{p}USER", "").strip(),
        "password": os.getenv(f"{p}PASSWORD", ""),
        "statement_timeout": int(os.getenv("CORE_DB_STATEMENT_TIMEOUT", "60") or 60),
        "max_rows": int(os.getenv("CORE_DB_MAX_ROWS", "200000") or 200000),
        "connect_timeout": int(os.getenv("CORE_DB_CONNECT_TIMEOUT", "10") or 10),
    }


def environment_options() -> list[dict]:
    """
    What the toggle can offer, and whether each is actually usable. An option
    that cannot connect is shown as unavailable rather than hidden, so a
    missing host reads as "not configured yet" instead of the toggle
    mysteriously having one side.
    """
    out = []
    for name in ENVIRONMENTS:
        cfg = core_db_config(name)
        out.append({
            "name": name,
            "configured": bool(cfg["host"] and cfg["dbname"] and cfg["user"]),
            "host": cfg["host"],
            "database": cfg["dbname"],
            "active": name == active_environment(),
        })
    return out


def core_db_status() -> dict:
    """
    Whether the switch is configured and reachable -- safe to send to the
    browser. Never includes the password, and reports the host only so an
    operator can tell which switch they are pointed at.
    """
    cfg = core_db_config()
    configured = bool(cfg["host"] and cfg["dbname"] and cfg["user"])
    status = {
        "environment": cfg["environment"],
        "configured": configured,
        "host": cfg["host"],
        "port": cfg["port"],
        "database": cfg["dbname"],
        "read_only": True,
        "reachable": False,
        "error": None,
        "latency_ms": None,
    }
    if not configured:
        prefix = _env_prefix(cfg["environment"])
        status["error"] = (
            f"{prefix}HOST / {prefix}NAME / {prefix}USER are not set in backend/.env"
        )
        return status

    started = time.perf_counter()
    try:
        rows = run_query("SELECT 1 AS ok")
        status["reachable"] = bool(rows)
        status["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator as text
        status["error"] = str(exc).strip().splitlines()[0][:300]
    return status


def connect() -> psycopg.Connection:
    """
    A read-only connection to the switch.

    `default_transaction_read_only=on` is set as a connection option rather
    than by a SET afterwards, so it is in force for the very first statement
    and cannot be turned off later without a `SET` -- which the guard refuses.
    """
    cfg = core_db_config()
    if not (cfg["host"] and cfg["dbname"] and cfg["user"]):
        p = _env_prefix(cfg["environment"])
        raise RuntimeError(
            f"The {cfg['environment'].upper()} switch is not configured. Set {p}HOST, "
            f"{p}NAME, {p}USER and {p}PASSWORD in backend/.env."
        )

    options = " ".join((
        "-c default_transaction_read_only=on",
        f"-c statement_timeout={cfg['statement_timeout'] * 1000}",
        "-c idle_in_transaction_session_timeout=30000",
    ))
    return psycopg.connect(
        host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
        user=cfg["user"], password=cfg["password"],
        connect_timeout=cfg["connect_timeout"],
        options=options,
        autocommit=False,
        row_factory=dict_row,
        application_name=f"smartqr-audit {cfg['environment']} (read-only)",
    )


def run_query(sql: str, params: dict | tuple | None = None, max_rows: int | None = None) -> list[dict]:
    """
    Run one SELECT and return its rows as dicts.

    `params` is passed to psycopg for server-side binding -- build queries with
    placeholders, never by formatting values into the SQL string.
    """
    assert_read_only(sql)
    cap = max_rows or core_db_config()["max_rows"]

    with connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchmany(cap)
        finally:
            # Nothing here writes, but leaving a transaction open against the
            # live switch holds locks and a backend slot.
            conn.rollback()
    return [dict(r) for r in rows]


def describe(schema: str, table: str) -> list[dict]:
    """Column names and types for one table -- used to map the switch's shape
    onto this app's own without guessing."""
    return run_query(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %(schema)s AND table_name = %(table)s
        ORDER BY ordinal_position
        """,
        {"schema": schema, "table": table},
    )
