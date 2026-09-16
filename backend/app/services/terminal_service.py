"""
Adding QR terminals to a merchant: an outlet, a PAG and a PAP for each one.

A terminal is three rows, and they have to agree with each other:

    shared.merchant_outlets   id = <new outlet id>, title = terminal name
    shared.merchant_pags      id = <new pag id>, outlet_id = the outlet above
    shared.merchant_paps      id = the SAME pag id, pag_id = it too,
                              outlet_id = the outlet above, tid = terminal name

So each terminal gets two fresh ids: one for its outlet, one shared by its PAG
and PAP. They are never the same value, and the outlet row must exist or the
PAG points at nothing.

This is the one part of the application that writes to the switch, and it is
kept apart from core_db deliberately. core_db is read-only three times over and
should stay that way: everything else in this app reports on the switch and has
no business changing it.

So this module opens its own connection, and that connection may insert into
exactly three tables and do nothing else. Any other statement is refused before
it is sent. The point is that widening what the app can write should require
editing this file, not just passing it different SQL.

Every new terminal copies the merchant's existing terminal for everything
except its name, which is what makes this safe to automate: the fields that
decide how money moves -- processor, payment modes, MCC, allowed transaction
types, member code -- are taken from a row the switch already accepted rather
than guessed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from app.services.core_db import active_environment, core_db_config

# The only tables this connection may write to, and the only operation.
_WRITABLE = (
    "shared.merchant_outlets",
    "shared.merchant_pags",
    "shared.merchant_paps",
)

SYSTEM_ACTOR = json.dumps({"label": "SYSTEM", "value": "SYSTEM"})


class TerminalError(Exception):
    """Anything that should stop a terminal being created, said plainly."""


def _assert_insert_only(sql: str) -> None:
    """
    Refuse anything that is not an INSERT into one of the allowed tables.

    Mirrors the read-only guard in core_db: an allowlist, checked before the
    statement is sent, because the cost of being wrong here is a write to a
    payment switch's merchant tables.
    """
    text = " ".join(sql.strip().lower().split())
    if not text.startswith("insert into "):
        raise TerminalError("This connection may only INSERT.")
    if ";" in text.rstrip().rstrip(";"):
        raise TerminalError("One statement at a time.")
    target = text[len("insert into "):].split("(")[0].strip()
    if target not in _WRITABLE:
        raise TerminalError(
            f"Refusing to write to {target!r}; only {', '.join(_WRITABLE)} are allowed."
        )


def _write_connection(environment: str) -> psycopg.Connection:
    """
    A connection that may write -- the only one in the application.

    Not read-only, obviously, but everything else from core_db's setup is kept:
    the statement timeout, the connect timeout, and an application_name that
    says plainly what this is, so anyone looking at pg_stat_activity on the
    switch can see the audit tool writing and know why.
    """
    cfg = core_db_config(environment)
    if not (cfg["host"] and cfg["dbname"] and cfg["user"]):
        raise TerminalError(f"The {environment.upper()} switch is not configured.")

    return psycopg.connect(
        host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
        user=cfg["user"], password=cfg["password"],
        connect_timeout=cfg["connect_timeout"],
        options=f"-c statement_timeout={cfg['statement_timeout'] * 1000}",
        autocommit=False,
        row_factory=dict_row,
        application_name=f"smartqr-audit {environment} (add terminal)",
    )


def _read(environment: str, sql: str, params: dict) -> list[dict]:
    """Read through the write connection, so the template and the insert see
    the same transaction -- a terminal added between reading and writing would
    otherwise be missed."""
    from app.services.core_db import run_query

    del environment  # reads go through the normal read-only path
    return run_query(sql, params)


# The merchant's newest complete terminal: outlet, PAG and PAP together. All
# three are needed, because all three are being copied -- a PAG whose outlet has
# been deleted is not a pattern worth following.
_TEMPLATE_SQL = """
SELECT
    g.id AS pag_id, g.type AS pag_type, g.name AS pag_name, g.floor_name,
    g.counter_name, g.group_type, g.member_code, g.contact_number,
    p.label, p.type AS pap_type, p.recurring_fee, p.mcc, p.risk,
    p.service_fee, p.processor, p.payment_modes, p.mid, p.allowed_txn_types,
    p.certificate, p.is_push_notification_enabled,
    o.id AS outlet_id, o.title AS outlet_title, o.type AS outlet_type,
    o.address_state, o.address_district, o.address_municipality,
    o.address_ward, o.address_street, o.contact_name,
    o.contact_number AS outlet_contact_number, o.contact_email,
    o.is_active AS outlet_is_active
FROM shared.merchant_pags g
JOIN shared.merchant_paps p ON p.pag_id = g.id
JOIN shared.merchant_outlets o ON o.id = g.outlet_id
WHERE g.gmid = %(mid)s AND COALESCE(g.deleted, false) = false
ORDER BY g.created_on DESC NULLS LAST
"""

_EXISTING_NAMES_SQL = """
SELECT name FROM shared.merchant_pags WHERE gmid = %(mid)s
"""


def terminal_template(mid: str) -> dict:
    """
    The merchant's most recent terminal, used as the pattern for new ones.

    Without one there is nothing to copy, and inventing processor or MCC values
    for a merchant is not something worth guessing at -- so this refuses rather
    than falling back to defaults.
    """
    mid = (mid or "").strip()
    if not mid:
        raise TerminalError("A MID is required.")

    rows = _read(active_environment(), _TEMPLATE_SQL, {"mid": mid})
    if not rows:
        raise TerminalError(
            f"{mid} has no complete existing terminal to copy -- an outlet, a PAG "
            "and a PAP that all point at each other. Create the first one on the "
            "switch, then this can add more like it."
        )
    return rows[0]


def existing_names(mid: str) -> list[str]:
    return [
        (r.get("name") or "").strip()
        for r in _read(active_environment(), _EXISTING_NAMES_SQL, {"mid": mid})
    ]


def suggest_names(mid: str, count: int) -> list[str]:
    """
    "Terminal 1", "Terminal 2"... skipping any the merchant already has, so
    running this twice does not produce two terminals with one name.
    """
    taken = {n.lower().replace(" ", "") for n in existing_names(mid)}
    out: list[str] = []
    n = 1
    while len(out) < count:
        candidate = f"Terminal {n}"
        if candidate.lower().replace(" ", "") not in taken:
            out.append(candidate)
            taken.add(candidate.lower().replace(" ", ""))
        n += 1
    return out


_INSERT_OUTLET = """
INSERT INTO shared.merchant_outlets (
    gmid, id, deleted, title, type, address_state, address_district,
    address_municipality, address_ward, address_street, contact_name,
    contact_number, contact_email, member_code, is_active, created_on,
    created_by, last_modified_on, last_modified_by, is_default
) VALUES (
    %(gmid)s, %(id)s, false, %(title)s, %(type)s, %(address_state)s,
    %(address_district)s, %(address_municipality)s, %(address_ward)s,
    %(address_street)s, %(contact_name)s, %(contact_number)s, %(contact_email)s,
    %(member_code)s, %(is_active)s, %(now)s, %(actor)s, %(now)s, %(actor)s,
    %(is_default)s
)
"""

_INSERT_PAG = """
INSERT INTO shared.merchant_pags (
    gmid, outlet_id, id, deleted, type, name, floor_name, counter_name,
    group_type, member_code, created_on, created_by, last_modified_on,
    last_modified_by, status, is_active, is_default, contact_number
) VALUES (
    %(gmid)s, %(outlet_id)s, %(id)s, false, %(type)s, %(name)s, %(floor_name)s,
    %(counter_name)s, %(group_type)s, %(member_code)s, %(now)s, %(actor)s,
    %(now)s, %(actor)s, null, true, %(is_default)s, %(contact_number)s
)
"""

_INSERT_PAP = """
INSERT INTO shared.merchant_paps (
    gmid, outlet_id, pag_id, id, is_active, deleted, label, type, recurring_fee,
    mcc, risk, service_fee, processor, payment_modes, mid, tid, mid_tid,
    allowed_txn_types, certificate, member_code, created_on, created_by,
    last_modified_on, last_modified_by, status, is_default,
    is_push_notification_enabled
) VALUES (
    %(gmid)s, %(outlet_id)s, %(pag_id)s, %(id)s, true, false, %(label)s,
    %(pap_type)s, %(recurring_fee)s, %(mcc)s, %(risk)s, %(service_fee)s,
    %(processor)s, %(payment_modes)s, %(mid)s, %(tid)s, null,
    %(allowed_txn_types)s, %(certificate)s, %(member_code)s, %(now)s, %(actor)s,
    %(now)s, %(actor)s, null, false, %(push)s
)
"""


def _plan_one(mid: str, name: str, tpl: dict, now: datetime) -> dict:
    """
    The three rows for one terminal.

    Two new ids: the outlet's, and one shared by the PAG and the PAP. The
    terminal's name appears in all three -- as the outlet title, the PAG name
    and the PAP's TID -- which is what makes one terminal recognisable across
    the three tables.
    """
    pag_id = str(uuid.uuid4())
    outlet_id = str(uuid.uuid4())
    return {
        "name": name,
        "id": pag_id,
        "outlet_id": outlet_id,
        "outlet": {
            "gmid": mid, "id": outlet_id, "title": name,
            "type": Json(tpl.get("outlet_type")),
            "address_state": Json(tpl.get("address_state")),
            "address_district": Json(tpl.get("address_district")),
            "address_municipality": Json(tpl.get("address_municipality")),
            "address_ward": tpl.get("address_ward"),
            "address_street": tpl.get("address_street"),
            "contact_name": tpl.get("contact_name"),
            "contact_number": tpl.get("outlet_contact_number"),
            "contact_email": tpl.get("contact_email"),
            "member_code": tpl.get("member_code"),
            "is_active": tpl.get("outlet_is_active") if tpl.get("outlet_is_active") is not None else True,
            "now": now, "actor": SYSTEM_ACTOR,
            # Which outlet is the merchant's default is a separate decision from
            # adding one, so a new outlet never claims it.
            "is_default": False,
        },
        "pag": {
            "gmid": mid, "outlet_id": outlet_id, "id": pag_id,
            "type": tpl.get("pag_type"), "name": name,
            "floor_name": tpl.get("floor_name"), "counter_name": tpl.get("counter_name"),
            "group_type": tpl.get("group_type"), "member_code": tpl.get("member_code"),
            "now": now, "actor": SYSTEM_ACTOR,
            "is_default": False,
            "contact_number": tpl.get("contact_number"),
        },
        "pap": {
            "gmid": mid, "outlet_id": outlet_id, "pag_id": pag_id, "id": pag_id,
            "label": tpl.get("label"), "pap_type": tpl.get("pap_type"),
            "recurring_fee": tpl.get("recurring_fee"),
            "mcc": Json(tpl.get("mcc")), "risk": tpl.get("risk"),
            "service_fee": tpl.get("service_fee"), "processor": tpl.get("processor"),
            "payment_modes": Json(tpl.get("payment_modes")),
            "mid": tpl.get("mid") or mid,
            "tid": name,
            "allowed_txn_types": Json(tpl.get("allowed_txn_types")),
            "certificate": tpl.get("certificate"),
            "member_code": tpl.get("member_code"),
            "now": now, "actor": SYSTEM_ACTOR,
            "push": bool(tpl.get("is_push_notification_enabled")),
        },
    }


class Json:
    """Marks a value as jsonb so psycopg adapts it rather than sending text."""

    def __init__(self, value):
        self.value = value


def _adapt(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        out[k] = psycopg.types.json.Jsonb(v.value) if isinstance(v, Json) else v
    return out


def plan_terminals(mid: str, names: list[str]) -> dict:
    """What would be created, without creating it."""
    tpl = terminal_template(mid)
    now = datetime.now()
    return {
        "mid": mid,
        "environment": active_environment(),
        "template": {
            "copied_from": tpl.get("pag_name"),
            "outlet_title": tpl.get("outlet_title"),
            "outlet_type": tpl.get("outlet_type"),
            "address_district": tpl.get("address_district"),
            "pag_type": tpl.get("pag_type"),
            "floor_name": tpl.get("floor_name"), "counter_name": tpl.get("counter_name"),
            "group_type": tpl.get("group_type"), "member_code": tpl.get("member_code"),
            "processor": tpl.get("processor"), "mcc": tpl.get("mcc"),
            "payment_modes": tpl.get("payment_modes"),
            "allowed_txn_types": tpl.get("allowed_txn_types"),
            "label": tpl.get("label"),
        },
        "existing_terminals": existing_names(mid),
        "terminals": [
            {"name": p["name"], "id": p["id"], "outlet_id": p["outlet_id"]}
            for p in (_plan_one(mid, n, tpl, now) for n in names)
        ],
    }


def create_terminals(mid: str, names: list[str], environment: str) -> dict:
    """
    Create the terminals, both rows each, all in one transaction.

    All or nothing, all three tables: an outlet with no PAG, or a PAG with no
    PAP, is a terminal that half exists and cannot take a payment. Worse than
    the request simply failing.
    """
    mid = (mid or "").strip()
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        raise TerminalError("At least one terminal name is required.")
    if len(names) != len(set(n.lower() for n in names)):
        raise TerminalError("Terminal names must differ from each other.")

    clash = {n.lower() for n in existing_names(mid)} & {n.lower() for n in names}
    if clash:
        raise TerminalError(
            f"{mid} already has a terminal called {', '.join(sorted(clash))}."
        )

    tpl = terminal_template(mid)
    now = datetime.now()
    plans = [_plan_one(mid, n, tpl, now) for n in names]

    created = []
    with _write_connection(environment) as conn:
        try:
            with conn.cursor() as cur:
                for p in plans:
                    # Outlet first: the PAG references it, so creating them the
                    # other way round would leave a window where the PAG points
                    # at nothing.
                    _assert_insert_only(_INSERT_OUTLET)
                    cur.execute(_INSERT_OUTLET, _adapt(p["outlet"]))
                    _assert_insert_only(_INSERT_PAG)
                    cur.execute(_INSERT_PAG, _adapt(p["pag"]))
                    _assert_insert_only(_INSERT_PAP)
                    cur.execute(_INSERT_PAP, _adapt(p["pap"]))
                    created.append({
                        "name": p["name"], "id": p["id"], "outlet_id": p["outlet_id"],
                    })
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "mid": mid,
        "environment": environment,
        "created": created,
        "count": len(created),
    }
