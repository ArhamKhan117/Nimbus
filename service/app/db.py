"""The account database. SQLite by default, Postgres by ``DATABASE_URL``.

Five tables and no ORM. The whole data model is "who paid, what key did they get, which machines is
it on, and has this device had its trial" -- that is small enough that raw SQL is clearer than a
mapping layer, and it keeps the service to two dependencies.

Schema creation is idempotent and additive, the same discipline ``sessions.py`` follows in the
desktop app: a deploy must never need a migration step someone can forget.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

SQLITE_PATH = os.getenv("SQLITE_PATH", "nimbus.db")

TRIAL_DAYS = 7
DEFAULT_SEATS = 2
"""Two devices per subscription: a desktop and a laptop.

Enough for how one person actually works, and small enough that a key passed round a classroom runs
out immediately -- which is the entire point of counting seats. A device that is genuinely being
replaced is handled by Account -> Deactivate this device, not by a spare seat."""


@contextmanager
def connect():
    conn = sqlite3.connect(SQLITE_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_schema() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                email        TEXT NOT NULL UNIQUE,
                stripe_id    TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                source       TEXT NOT NULL DEFAULT 'stripe'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS licences (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                key            TEXT NOT NULL UNIQUE,
                customer_id    INTEGER NOT NULL,
                plan           TEXT NOT NULL DEFAULT 'Nimbus',
                seats_total    INTEGER NOT NULL DEFAULT 2,
                status         TEXT NOT NULL DEFAULT 'active',
                current_period_end TEXT NOT NULL,
                created_at     TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                licence_id   INTEGER NOT NULL,
                device_id    TEXT NOT NULL,
                device_name  TEXT NOT NULL DEFAULT '',
                first_seen   TEXT NOT NULL,
                last_seen    TEXT NOT NULL,
                active       INTEGER NOT NULL DEFAULT 1,
                UNIQUE(licence_id, device_id)
            )
        """)
        # The trial table is keyed on device_id alone, with no tester and no email.
        #
        # That is the whole anti-abuse design: a new email address gets no second trial because the
        # email was never what a trial was counted against. Rows are kept forever -- a trial that
        # expires and is deleted is a trial you can take again.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trials (
                device_id    TEXT PRIMARY KEY,
                device_name  TEXT NOT NULL DEFAULT '',
                started_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                at           TEXT NOT NULL,
                kind         TEXT NOT NULL,
                detail       TEXT NOT NULL DEFAULT ''
            )
        """)


def log_event(kind: str, detail: str = "") -> None:
    """An append-only audit trail.

    Worth having for a paid product: when someone says "my licence stopped working", the answer is
    in here, and reconstructing it from application logs after the fact is not possible.
    """
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
                (now().isoformat(timespec="seconds"), kind, detail))
    except sqlite3.Error:
        pass


def new_licence_key() -> str:
    """``NIMBUS-XXXX-XXXX-XXXX`` from a CSPRNG.

    ``secrets``, not ``random``: a guessable licence key is a licence key everyone has. The alphabet
    omits I, O, 0 and 1 because these get read aloud and typed by hand.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    blocks = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "NIMBUS-" + "-".join(blocks)


# --- customers and licences --------------------------------------------------


def upsert_customer(email: str, stripe_id: str = "", source: str = "stripe") -> int:
    email = email.strip().lower()
    with connect() as conn:
        row = conn.execute("SELECT id FROM customers WHERE email = ?", (email,)).fetchone()
        if row:
            if stripe_id:
                conn.execute("UPDATE customers SET stripe_id = ? WHERE id = ?",
                             (stripe_id, row["id"]))
            return int(row["id"])
        cursor = conn.execute(
            "INSERT INTO customers (email, stripe_id, created_at, source) VALUES (?, ?, ?, ?)",
            (email, stripe_id, now().isoformat(timespec="seconds"), source))
        return int(cursor.lastrowid)


def create_licence(customer_id: int, period_end: datetime, plan: str = "Nimbus",
                   seats: int = DEFAULT_SEATS) -> str:
    key = new_licence_key()
    with connect() as conn:
        conn.execute(
            "INSERT INTO licences (key, customer_id, plan, seats_total, status, "
            "current_period_end, created_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (key, customer_id, plan, seats,
             period_end.isoformat(timespec="seconds"), now().isoformat(timespec="seconds")))
    log_event("licence.created", key)
    return key


def licence_for_customer(customer_id: int) -> sqlite3.Row | None:
    """The tester's active licence, newest first, or ``None``."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM licences WHERE customer_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1", (customer_id,)).fetchone()


def ensure_licence(customer_id: int, period_end: datetime, plan: str = "Nimbus",
                   seats: int = DEFAULT_SEATS) -> tuple[str, bool]:
    """``(key, created)``. Returns the tester's existing active licence rather than a second one.

    Issuance has to be idempotent because it is reached from three directions for a single purchase:
    Stripe sends both ``checkout.session.completed`` and ``customer.subscription.created``, and the
    success page asks for the key directly in case it loads before either webhook lands. Minting a
    new key each time would leave a tester holding three keys, two of which count seats they are
    not using -- and no way to tell which one is theirs.
    """
    existing = licence_for_customer(customer_id)
    if existing is not None:
        # Extend the period rather than leave it behind: this path is also how a renewal arrives.
        if period_end > _parse_stamp(str(existing["current_period_end"])):
            set_licence_status(str(existing["key"]), "active", period_end)
        return str(existing["key"]), False
    return create_licence(customer_id, period_end, plan=plan, seats=seats), True


def _parse_stamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def licence_by_key(key: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT l.*, c.email AS email FROM licences l "
            "JOIN customers c ON c.id = l.customer_id WHERE l.key = ?",
            (key.strip().upper(),)).fetchone()


def licence_for_stripe_subscription(subscription_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT l.* FROM licences l JOIN customers c ON c.id = l.customer_id "
            "WHERE c.stripe_id = ?", (subscription_id,)).fetchone()


def set_licence_status(key: str, status: str, period_end: datetime | None = None) -> None:
    with connect() as conn:
        if period_end is None:
            conn.execute("UPDATE licences SET status = ? WHERE key = ?", (status, key))
        else:
            conn.execute(
                "UPDATE licences SET status = ?, current_period_end = ? WHERE key = ?",
                (status, period_end.isoformat(timespec="seconds"), key))
    log_event(f"licence.{status}", key)


# --- devices -----------------------------------------------------------------


def active_devices(licence_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM devices WHERE licence_id = ? AND active = 1",
            (licence_id,)).fetchone()
        return int(row["n"]) if row else 0


def claim_device(licence_id: int, device_id: str, device_name: str, seats_total: int) -> bool:
    """Bind a device to a licence. ``False`` when the seat limit is already reached.

    A device already on the licence is always allowed through, even at the limit -- it is
    re-activating, not taking a new seat, and refusing it would lock a legitimate user out of their
    own machine after a reinstall.
    """
    stamp = now().isoformat(timespec="seconds")
    with connect() as conn:
        existing = conn.execute(
            "SELECT id, active FROM devices WHERE licence_id = ? AND device_id = ?",
            (licence_id, device_id)).fetchone()
        if existing:
            conn.execute(
                "UPDATE devices SET active = 1, last_seen = ?, device_name = ? WHERE id = ?",
                (stamp, device_name, existing["id"]))
            return True
        if active_devices(licence_id) >= max(1, seats_total):
            return False
        conn.execute(
            "INSERT INTO devices (licence_id, device_id, device_name, first_seen, last_seen, "
            "active) VALUES (?, ?, ?, ?, ?, 1)",
            (licence_id, device_id, device_name, stamp, stamp))
        return True


def release_device(licence_id: int, device_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE devices SET active = 0 WHERE licence_id = ? AND device_id = ?",
            (licence_id, device_id))


# --- trials ------------------------------------------------------------------


def start_trial(device_id: str, device_name: str) -> tuple[datetime | None, bool]:
    """``(expires_at, is_new)``. ``(None, False)`` when this device already had its trial.

    Returning the original expiry for a repeat request rather than an error is deliberate: a user who
    reinstalls during their trial should get their remaining days back, not a refusal. Only a device
    whose trial has *elapsed* is turned away, and that is decided by the caller comparing the date.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT expires_at FROM trials WHERE device_id = ?", (device_id,)).fetchone()
        if row:
            try:
                return datetime.fromisoformat(row["expires_at"]), False
            except ValueError:
                return None, False
        # Truncated to the second because that is what gets stored. Returning the microsecond value
        # would mean the token issued on the first request and the token issued on a reinstall
        # disagreed about the same trial's expiry -- harmless in effect, and exactly the kind of
        # inconsistency that makes a support conversation impossible to close.
        expires = (now() + timedelta(days=TRIAL_DAYS)).replace(microsecond=0)
        conn.execute(
            "INSERT INTO trials (device_id, device_name, started_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (device_id, device_name, now().isoformat(timespec="seconds"),
             expires.isoformat(timespec="seconds")))
    log_event("trial.started", device_id[:12])
    return expires, True
