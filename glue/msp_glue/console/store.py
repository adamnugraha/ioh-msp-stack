"""The console's own data: the audit trail and the signed-in sessions, in one SQLite file.

The file lives in the console_data volume (CONSOLE_DATA_DIR, default /data/console.db) and is part of
the nightly backup. Nothing in it is a password: sessions hold Keycloak tokens that only work together
with the console's client secret, and they expire with the Keycloak session.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime

from ..settings import settings
from .data import tz

_lock = threading.Lock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    email     TEXT NOT NULL,
    name      TEXT NOT NULL DEFAULT '',
    action    TEXT NOT NULL,
    customer  TEXT NOT NULL DEFAULT '',
    target    TEXT NOT NULL DEFAULT '',
    ticket    TEXT NOT NULL DEFAULT '',
    reason    TEXT NOT NULL DEFAULT '',
    outcome   TEXT NOT NULL DEFAULT '',
    result    TEXT NOT NULL DEFAULT '',
    detail    TEXT NOT NULL DEFAULT '{}',
    address   TEXT NOT NULL DEFAULT '',
    updated   REAL
);
CREATE INDEX IF NOT EXISTS audit_at ON audit (at);
CREATE INDEX IF NOT EXISTS audit_customer ON audit (customer, at);
CREATE TABLE IF NOT EXISTS sessions (
    sid        TEXT PRIMARY KEY,
    sub        TEXT NOT NULL,
    email      TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    roles      TEXT NOT NULL DEFAULT '[]',
    id_token   TEXT NOT NULL DEFAULT '',
    refresh    TEXT NOT NULL DEFAULT '',
    created    REAL NOT NULL,
    checked    REAL NOT NULL,
    expires    REAL NOT NULL,
    address    TEXT NOT NULL DEFAULT ''
);
"""
# Columns added after 1.1: (table, column, definition)
MIGRATIONS = [("sessions", "address", "TEXT NOT NULL DEFAULT ''")]


def path():
    return settings.console_data_dir / "console.db"


@contextmanager
def _db():
    global _ready
    if not _ready:
        with _lock:
            if not _ready:
                settings.console_data_dir.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(path()) as conn:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(SCHEMA)
                    for table, column, definition in MIGRATIONS:
                        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                        if column not in have:
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                _ready = True
    conn = sqlite3.connect(path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# =============================================================================
# Audit trail
# =============================================================================
def record(user: dict, action: str, *, customer: str = "", target: str = "", ticket: str = "", reason: str = "",
           outcome: str = "", result: str = "", detail: dict | None = None, address: str = "") -> int:
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO audit (at, email, name, action, customer, target, ticket, reason, outcome, result, detail,"
            " address) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), user.get("email", ""), user.get("name", ""), action, customer, target, ticket,
             reason[:1000], outcome, result[:4000], json.dumps(detail or {}), address))
        return int(cur.lastrowid)


def update(entry_id: int, outcome: str, result: str | None = None, detail: dict | None = None) -> None:
    with _db() as conn:
        row = conn.execute("SELECT result, detail FROM audit WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return
        merged = {**json.loads(row["detail"] or "{}"), **(detail or {})}
        conn.execute("UPDATE audit SET outcome = ?, result = ?, detail = ?, updated = ? WHERE id = ?",
                     (outcome, (row["result"] if result is None else result)[:4000], json.dumps(merged),
                      time.time(), entry_id))


def set_ticket(entry_id: int, reference: str) -> None:
    """The ticket or change an entry is about, once the tool has given it a number."""
    with _db() as conn:
        conn.execute("UPDATE audit SET ticket = ? WHERE id = ?", (reference, entry_id))


def _row(r: sqlite3.Row) -> dict:
    out = dict(r)
    out["at"] = datetime.fromtimestamp(r["at"], tz())
    out["updated"] = datetime.fromtimestamp(r["updated"], tz()) if r["updated"] else None
    out["detail"] = json.loads(r["detail"] or "{}")
    return out


def entries(limit: int = 200, customer: str = "", email: str = "", action: str = "",
            since: float | None = None, outcome: str = "") -> list[dict]:
    query, args = "SELECT * FROM audit WHERE 1=1", []
    for column, value in (("customer", customer), ("email", email), ("action", action), ("outcome", outcome)):
        if value:
            query += f" AND {column} = ?"
            args.append(value)
    if since:
        query += " AND at >= ?"
        args.append(since)
    query += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _db() as conn:
        return [_row(r) for r in conn.execute(query, args).fetchall()]


def unfinished(action: str, max_age: float) -> list[dict]:
    """Entries still waiting for a result (for example a playbook run when the console restarted)."""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM audit WHERE action = ? AND outcome IN ('started', 'queued') AND at >= ?",
                            (action, time.time() - max_age)).fetchall()
    return [_row(r) for r in rows]


def distinct(column: str) -> list[str]:
    assert column in ("email", "action", "customer", "outcome")
    with _db() as conn:
        return [r[0] for r in conn.execute(f"SELECT DISTINCT {column} FROM audit WHERE {column} != '' ORDER BY 1")]


# =============================================================================
# Sessions
# =============================================================================
def save_session(sid: str, user: dict, id_token: str, refresh: str, expires: float, address: str = "") -> None:
    now = time.time()
    with _db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        conn.execute(
            "INSERT OR REPLACE INTO sessions (sid, sub, email, name, roles, id_token, refresh, created, checked, expires,"
            " address) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, user["sub"], user["email"], user["name"], json.dumps(user["roles"]), id_token, refresh, now, now,
             expires, address[:120]))


def load_session(sid: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE sid = ? AND expires > ?", (sid, time.time())).fetchone()
    if not row:
        return None
    out = dict(row)
    out["roles"] = json.loads(row["roles"] or "[]")
    return out


def refresh_session(sid: str, user: dict, refresh: str, id_token: str = "", expires: float | None = None) -> None:
    """After a successful check with Keycloak: new tokens, current roles and, when given, a new end time."""
    with _db() as conn:
        conn.execute("UPDATE sessions SET email = ?, name = ?, roles = ?, refresh = ?, checked = ?,"
                     " id_token = CASE WHEN ? != '' THEN ? ELSE id_token END,"
                     " expires = COALESCE(?, expires) WHERE sid = ?",
                     (user["email"], user["name"], json.dumps(user["roles"]), refresh, time.time(), id_token,
                      id_token, expires, sid))


def touch_session(sid: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE sessions SET checked = ? WHERE sid = ?", (time.time(), sid))


def delete_session(sid: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM sessions WHERE sid = ?", (sid,))


def active_sessions() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT email, name, roles, created, checked, expires, address FROM sessions"
                            " WHERE expires > ? ORDER BY checked DESC", (time.time(),)).fetchall()
    return [{"email": r["email"], "name": r["name"], "roles": json.loads(r["roles"] or "[]"),
             "created": datetime.fromtimestamp(r["created"], tz()), "checked": datetime.fromtimestamp(r["checked"], tz()),
             "expires": datetime.fromtimestamp(r["expires"], tz()), "address": r["address"] or ""}
            for r in rows]
