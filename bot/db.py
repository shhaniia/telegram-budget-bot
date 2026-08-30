"""SQLite data layer. Plain sqlite3 (no ORM) — the schema is small enough
that a thin wrapper is clearer than pulling in SQLAlchemy. Every function
opens and closes its own short-lived connection, which is fine at this
scale and keeps things safe across the async handlers and the background
job queue.

Every table is scoped by Telegram user_id (categories included), so
multiple people can use the same bot with completely separate data —
required now that the bot runs in public mode by default.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from bot import config, timeutil

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    is_necessity INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    description TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    purchase_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    amount REAL NOT NULL,
    period TEXT NOT NULL DEFAULT 'monthly',
    UNIQUE(user_id, category_id, period)
);

CREATE TABLE IF NOT EXISTS thresholds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    category_id INTEGER,
    percent INTEGER NOT NULL,
    UNIQUE(user_id, category_id, percent)
);

CREATE TABLE IF NOT EXISTS threshold_alerts_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    period_key TEXT NOT NULL,
    percent INTEGER NOT NULL,
    UNIQUE(user_id, category_id, period_key, percent)
);

CREATE TABLE IF NOT EXISTS recurring_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    frequency TEXT NOT NULL DEFAULT 'monthly',
    is_necessity INTEGER NOT NULL DEFAULT 1,
    next_due TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'monthly',
    next_billing_date TEXT NOT NULL,
    reminder_days_before INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'active',
    last_reminded_for TEXT
);

CREATE TABLE IF NOT EXISTS pending_confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    amount REAL,
    category_id INTEGER,
    description TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    daily_reminder_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS budget_pace_status (
    user_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    streak INTEGER NOT NULL DEFAULT 0,
    last_checkin_key TEXT,
    PRIMARY KEY (user_id, category_id)
);
"""


def _connect() -> sqlite3.Connection:
    Path(config.DATABASE_PATH).expanduser().resolve().parent.mkdir(
        parents=True, exist_ok=True
    )
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate_categories_to_per_user(conn: sqlite3.Connection) -> None:
    """One-time, idempotent migration for databases created before
    categories were per-user. Existing (global) categories get assigned to
    whichever user_id already has purchases/budgets/etc — the original
    single owner of that data — so nothing already in production breaks."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(categories)").fetchall()]
    if "user_id" in cols:
        return  # already on the new schema

    owner_row = conn.execute("SELECT user_id FROM purchases ORDER BY id LIMIT 1").fetchone()
    if owner_row is None:
        owner_row = conn.execute("SELECT user_id FROM budgets ORDER BY id LIMIT 1").fetchone()
    owner = owner_row["user_id"] if owner_row else 0

    conn.executescript(
        f"""
        CREATE TABLE categories_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE,
            is_necessity INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, name)
        );
        INSERT INTO categories_new (id, user_id, name, is_necessity)
            SELECT id, {owner}, name, is_necessity FROM categories;
        DROP TABLE categories;
        ALTER TABLE categories_new RENAME TO categories;
        """
    )


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate_categories_to_per_user(conn)


def ensure_categories_seeded(user_id: int, conn=None) -> None:
    """Gives a user their own default category set the first time they're
    seen. Safe to call on every incoming message — it's a no-op once
    they already have at least one category."""

    def _run(c):
        existing = c.execute(
            "SELECT COUNT(*) AS n FROM categories WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        if existing == 0:
            c.executemany(
                "INSERT INTO categories (user_id, name, is_necessity) VALUES (?, ?, ?)",
                [(user_id, name, necessity) for name, necessity in config.DEFAULT_CATEGORIES],
            )

    if conn is not None:
        _run(conn)
        return
    with get_conn() as c:
        _run(c)


# ---------------------------------------------------------------- categories

def list_categories(user_id: int, conn=None) -> list[sqlite3.Row]:
    def _run(c):
        return c.execute(
            "SELECT * FROM categories WHERE user_id = ? ORDER BY name", (user_id,)
        ).fetchall()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def get_category_by_name(user_id: int, name: str, conn=None) -> sqlite3.Row | None:
    def _run(c):
        return c.execute(
            "SELECT * FROM categories WHERE user_id = ? AND name = ? COLLATE NOCASE",
            (user_id, name),
        ).fetchone()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def get_category_by_id(user_id: int, category_id: int, conn=None) -> sqlite3.Row | None:
    """Always scoped by user_id too, so one person can never look up (or
    accidentally act on) another person's category by guessing its id."""

    def _run(c):
        return c.execute(
            "SELECT * FROM categories WHERE id = ? AND user_id = ?", (category_id, user_id)
        ).fetchone()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def add_category(user_id: int, name: str, is_necessity: bool = False) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO categories (user_id, name, is_necessity) VALUES (?, ?, ?)",
            (user_id, name.strip(), int(is_necessity)),
        )
        return cur.lastrowid


def find_category_in_text(user_id: int, text: str, conn=None) -> sqlite3.Row | None:
    """Best-effort match: does any category name appear as a whole word in text?"""
    cats = list_categories(user_id, conn)
    lowered = text.lower()
    words = set(lowered.replace(",", " ").split())
    for cat in cats:
        name_lower = cat["name"].lower()
        if name_lower in lowered or name_lower in words:
            return cat
        # also match on individual words of multi-word category names
        for part in name_lower.split():
            if part in words and len(part) > 3:
                return cat
    return None


# ----------------------------------------------------------------- purchases

def add_purchase(
    user_id: int,
    amount: float,
    category_id: int,
    description: str = "",
    source: str = "manual",
    purchase_date: str | None = None,
) -> int:
    purchase_date = purchase_date or timeutil.today_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO purchases
               (user_id, amount, category_id, description, source, created_at, purchase_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                amount,
                category_id,
                description,
                source,
                timeutil.now().isoformat(),
                purchase_date,
            ),
        )
        return cur.lastrowid


def update_purchase_category(purchase_id: int, user_id: int, category_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE purchases SET category_id = ? WHERE id = ? AND user_id = ?",
            (category_id, purchase_id, user_id),
        )
        return cur.rowcount > 0


def get_purchase(purchase_id: int, user_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM purchases WHERE id = ? AND user_id = ?", (purchase_id, user_id)
        ).fetchone()


def delete_purchase(purchase_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM purchases WHERE id = ? AND user_id = ?", (purchase_id, user_id)
        )
        return cur.rowcount > 0


def spend_for_category_period(
    user_id: int, category_id: int, period: str, conn=None
) -> float:
    start, end = timeutil.period_bounds(period)

    def _run(c):
        row = c.execute(
            """SELECT COALESCE(SUM(amount), 0) AS total FROM purchases
               WHERE user_id = ? AND category_id = ?
                 AND purchase_date BETWEEN ? AND ?""",
            (user_id, category_id, start.isoformat(), end.isoformat()),
        ).fetchone()
        return row["total"]

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def spend_summary(user_id: int, start, end, conn=None) -> list[sqlite3.Row]:
    def _run(c):
        return c.execute(
            """SELECT categories.id AS category_id, categories.name AS category,
                      categories.is_necessity AS is_necessity,
                      SUM(purchases.amount) AS total, COUNT(*) AS n
               FROM purchases
               JOIN categories ON categories.id = purchases.category_id
               WHERE purchases.user_id = ? AND purchase_date BETWEEN ? AND ?
               GROUP BY categories.id
               ORDER BY total DESC""",
            (user_id, start.isoformat(), end.isoformat()),
        ).fetchall()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def recent_purchases(user_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT purchases.*, categories.name AS category_name
               FROM purchases JOIN categories ON categories.id = purchases.category_id
               WHERE purchases.user_id = ?
               ORDER BY purchases.id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()


def purchases_for_date(user_id: int, date_iso: str) -> list[sqlite3.Row]:
    """Every purchase logged on a given day, ordered category-then-item so
    a caller can group them straight off the list (used by /recent)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT purchases.*, categories.name AS category_name
               FROM purchases JOIN categories ON categories.id = purchases.category_id
               WHERE purchases.user_id = ? AND purchases.purchase_date = ?
               ORDER BY categories.name COLLATE NOCASE, purchases.id""",
            (user_id, date_iso),
        ).fetchall()


# ------------------------------------------------------------------- budgets

def set_budget(user_id: int, category_id: int, amount: float, period: str = "monthly") -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO budgets (user_id, category_id, amount, period)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, category_id, period)
               DO UPDATE SET amount = excluded.amount""",
            (user_id, category_id, amount, period),
        )


def list_budgets(user_id: int, conn=None) -> list[sqlite3.Row]:
    def _run(c):
        return c.execute(
            """SELECT budgets.*, categories.name AS category_name
               FROM budgets JOIN categories ON categories.id = budgets.category_id
               WHERE budgets.user_id = ?
               ORDER BY categories.name""",
            (user_id,),
        ).fetchall()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def get_budget(user_id: int, category_id: int, period: str = "monthly", conn=None):
    def _run(c):
        return c.execute(
            "SELECT * FROM budgets WHERE user_id = ? AND category_id = ? AND period = ?",
            (user_id, category_id, period),
        ).fetchone()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


# ---------------------------------------------------------------- thresholds

def set_threshold(user_id: int, percent: int, category_id: int | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO thresholds (user_id, category_id, percent)
               VALUES (?, ?, ?)""",
            (user_id, category_id, percent),
        )


def thresholds_for_category(user_id: int, category_id: int, conn=None) -> list[int]:
    def _run(c):
        rows = c.execute(
            """SELECT percent FROM thresholds
               WHERE user_id = ? AND (category_id = ? OR category_id IS NULL)""",
            (user_id, category_id),
        ).fetchall()
        return sorted({r["percent"] for r in rows})

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def has_any_threshold(user_id: int, conn=None) -> bool:
    def _run(c):
        return c.execute("SELECT 1 FROM thresholds WHERE user_id = ? LIMIT 1", (user_id,)).fetchone() is not None

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def was_alert_sent(user_id: int, category_id: int, period_key: str, percent: int, conn=None) -> bool:
    def _run(c):
        return (
            c.execute(
                """SELECT 1 FROM threshold_alerts_sent
                   WHERE user_id = ? AND category_id = ? AND period_key = ? AND percent = ?""",
                (user_id, category_id, period_key, percent),
            ).fetchone()
            is not None
        )

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def record_alert_sent(user_id: int, category_id: int, period_key: str, percent: int, conn=None) -> None:
    def _run(c):
        c.execute(
            """INSERT OR IGNORE INTO threshold_alerts_sent
               (user_id, category_id, period_key, percent) VALUES (?, ?, ?, ?)""",
            (user_id, category_id, period_key, percent),
        )

    if conn is not None:
        _run(conn)
        return
    with get_conn() as c:
        _run(c)


# ------------------------------------------------------------ recurring items

def add_recurring(
    user_id: int,
    name: str,
    amount: float,
    category_id: int,
    frequency: str,
    is_necessity: bool,
    next_due: str,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO recurring_items
               (user_id, name, amount, category_id, frequency, is_necessity, next_due, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (user_id, name, amount, category_id, frequency, int(is_necessity), next_due),
        )
        return cur.lastrowid


def list_recurring(user_id: int, active_only: bool = True) -> list[sqlite3.Row]:
    q = """SELECT recurring_items.*, categories.name AS category_name
           FROM recurring_items JOIN categories ON categories.id = recurring_items.category_id
           WHERE recurring_items.user_id = ?"""
    params: list = [user_id]
    if active_only:
        q += " AND active = 1"
    q += " ORDER BY next_due"
    with get_conn() as conn:
        return conn.execute(q, params).fetchall()


def get_recurring_by_name(user_id: int, name: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM recurring_items
               WHERE user_id = ? AND name = ? COLLATE NOCASE AND active = 1""",
            (user_id, name),
        ).fetchone()


def deactivate_recurring(user_id: int, name: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE recurring_items SET active = 0
               WHERE user_id = ? AND name = ? COLLATE NOCASE""",
            (user_id, name),
        )
        return cur.rowcount > 0


def due_recurring(as_of: str, conn=None) -> list[sqlite3.Row]:
    def _run(c):
        return c.execute(
            """SELECT recurring_items.*, categories.name AS category_name
               FROM recurring_items JOIN categories ON categories.id = recurring_items.category_id
               WHERE active = 1 AND next_due <= ?""",
            (as_of,),
        ).fetchall()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def advance_recurring_due(recurring_id: int, new_next_due: str, conn=None) -> None:
    def _run(c):
        c.execute(
            "UPDATE recurring_items SET next_due = ? WHERE id = ?",
            (new_next_due, recurring_id),
        )

    if conn is not None:
        _run(conn)
        return
    with get_conn() as c:
        _run(c)


# ------------------------------------------------------------- subscriptions

def add_subscription(
    user_id: int,
    name: str,
    amount: float,
    next_billing_date: str,
    frequency: str = "monthly",
    reminder_days_before: int = 3,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO subscriptions
               (user_id, name, amount, frequency, next_billing_date, reminder_days_before, status)
               VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            (user_id, name, amount, frequency, next_billing_date, reminder_days_before),
        )
        return cur.lastrowid


def list_subscriptions(user_id: int, active_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM subscriptions WHERE user_id = ?"
    params: list = [user_id]
    if active_only:
        q += " AND status = 'active'"
    q += " ORDER BY next_billing_date"
    with get_conn() as conn:
        return conn.execute(q, params).fetchall()


def get_subscription_by_name(user_id: int, name: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM subscriptions
               WHERE user_id = ? AND name = ? COLLATE NOCASE AND status = 'active'""",
            (user_id, name),
        ).fetchone()


def get_subscription_by_id(sub_id: int, conn=None) -> sqlite3.Row | None:
    def _run(c):
        return c.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def cancel_subscription(user_id: int, name: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE subscriptions SET status = 'cancelled'
               WHERE user_id = ? AND name = ? COLLATE NOCASE AND status = 'active'""",
            (user_id, name),
        )
        return cur.rowcount > 0


def cancel_subscription_by_id(sub_id: int, conn=None) -> None:
    def _run(c):
        c.execute("UPDATE subscriptions SET status = 'cancelled' WHERE id = ?", (sub_id,))

    if conn is not None:
        _run(conn)
        return
    with get_conn() as c:
        _run(c)


def list_all_active_subscriptions(conn=None) -> list[sqlite3.Row]:
    def _run(c):
        return c.execute("SELECT * FROM subscriptions WHERE status = 'active'").fetchall()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def due_subscription_reminders(reminder_cutoff_iso: str, conn=None) -> list[sqlite3.Row]:
    """Active subscriptions whose next_billing_date is within reminder window
    and haven't already been reminded for this billing cycle."""

    def _run(c):
        rows = c.execute(
            """SELECT * FROM subscriptions
               WHERE status = 'active' AND next_billing_date <= ?"""
        , (reminder_cutoff_iso,)).fetchall()
        return [r for r in rows if r["last_reminded_for"] != r["next_billing_date"]]

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def mark_subscription_reminded(sub_id: int, billing_date: str, conn=None) -> None:
    def _run(c):
        c.execute(
            "UPDATE subscriptions SET last_reminded_for = ? WHERE id = ?",
            (billing_date, sub_id),
        )

    if conn is not None:
        _run(conn)
        return
    with get_conn() as c:
        _run(c)


def due_subscription_charges(as_of: str, conn=None) -> list[sqlite3.Row]:
    def _run(c):
        return c.execute(
            "SELECT * FROM subscriptions WHERE status = 'active' AND next_billing_date <= ?",
            (as_of,),
        ).fetchall()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def advance_subscription_billing(sub_id: int, new_next_billing_date: str, conn=None) -> None:
    def _run(c):
        c.execute(
            "UPDATE subscriptions SET next_billing_date = ? WHERE id = ?",
            (new_next_billing_date, sub_id),
        )

    if conn is not None:
        _run(conn)
        return
    with get_conn() as c:
        _run(c)


def monthly_subscription_total(user_id: int) -> float:
    subs = list_subscriptions(user_id, active_only=True)
    total = 0.0
    for s in subs:
        if s["frequency"] == "yearly":
            total += s["amount"] / 12
        elif s["frequency"] == "weekly":
            total += s["amount"] * 52 / 12
        else:
            total += s["amount"]
    return total


# ---------------------------------------------------------- pending confirms

def create_pending(
    user_id: int,
    chat_id: int,
    amount: float | None,
    category_id: int | None,
    description: str,
    source: str,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO pending_confirmations
               (user_id, chat_id, amount, category_id, description, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, chat_id, amount, category_id, description, source, timeutil.now().isoformat()),
        )
        return cur.lastrowid


def get_pending(pending_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_confirmations WHERE id = ?", (pending_id,)
        ).fetchone()


def update_pending_category(pending_id: int, category_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_confirmations SET category_id = ? WHERE id = ?",
            (category_id, pending_id),
        )


def update_pending_amount(pending_id: int, amount: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_confirmations SET amount = ? WHERE id = ?",
            (amount, pending_id),
        )


def delete_pending(pending_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM pending_confirmations WHERE id = ?", (pending_id,))


# ------------------------------------------------------------- user settings

def get_daily_reminder_enabled(user_id: int, conn=None) -> bool:
    def _run(c):
        row = c.execute(
            "SELECT daily_reminder_enabled FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row["daily_reminder_enabled"]) if row else False

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def set_daily_reminder(user_id: int, enabled: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO user_settings (user_id, daily_reminder_enabled) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET daily_reminder_enabled = excluded.daily_reminder_enabled""",
            (user_id, int(enabled)),
        )


def list_users_with_reminder_enabled(conn=None) -> list[int]:
    def _run(c):
        rows = c.execute(
            "SELECT user_id FROM user_settings WHERE daily_reminder_enabled = 1"
        ).fetchall()
        return [r["user_id"] for r in rows]

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def purchase_count_for_date(user_id: int, date_iso: str, conn=None) -> int:
    def _run(c):
        row = c.execute(
            "SELECT COUNT(*) AS n FROM purchases WHERE user_id = ? AND purchase_date = ?",
            (user_id, date_iso),
        ).fetchone()
        return row["n"]

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


# ------------------------------------------------------------ budget pacing

def list_users_with_monthly_budgets(conn=None) -> list[int]:
    def _run(c):
        rows = c.execute(
            "SELECT DISTINCT user_id FROM budgets WHERE period = 'monthly'"
        ).fetchall()
        return [r["user_id"] for r in rows]

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def get_pace_status(user_id: int, category_id: int, conn=None) -> sqlite3.Row | None:
    def _run(c):
        return c.execute(
            "SELECT * FROM budget_pace_status WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        ).fetchone()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def nudge_worthy_pace_statuses(user_id: int, min_streak: int, conn=None) -> list[sqlite3.Row]:
    """Categories currently sitting on an over/under-pace streak long
    enough to be worth surfacing again outside the automatic check-in —
    used by /whatsnext so a nudge isn't only ever seen on check-in day."""

    def _run(c):
        return c.execute(
            """SELECT budget_pace_status.*, categories.name AS category_name
               FROM budget_pace_status JOIN categories ON categories.id = budget_pace_status.category_id
               WHERE budget_pace_status.user_id = ? AND budget_pace_status.status != 'within'
                 AND budget_pace_status.streak >= ?
               ORDER BY budget_pace_status.streak DESC""",
            (user_id, min_streak),
        ).fetchall()

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def upsert_pace_status(
    user_id: int, category_id: int, status: str, streak: int, checkin_key: str, conn=None
) -> None:
    def _run(c):
        c.execute(
            """INSERT INTO budget_pace_status (user_id, category_id, status, streak, last_checkin_key)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, category_id) DO UPDATE SET
                   status = excluded.status, streak = excluded.streak, last_checkin_key = excluded.last_checkin_key""",
            (user_id, category_id, status, streak, checkin_key),
        )

    if conn is not None:
        _run(conn)
        return
    with get_conn() as c:
        _run(c)
