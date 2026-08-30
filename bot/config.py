"""Loads and validates configuration from environment variables (.env)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

_raw_allowed = os.environ.get("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(uid.strip()) for uid in _raw_allowed.split(",") if uid.strip().isdigit()
}
# An empty allow-list means the bot is public: anyone on Telegram who finds
# it can use it, each person gets their own private categories/budgets/data
# (everything in the database is scoped by Telegram user_id). Set
# ALLOWED_USER_IDS to go back to private/invite-only mode.
PUBLIC_MODE = len(ALLOWED_USER_IDS) == 0

DATABASE_PATH = os.environ.get("DATABASE_PATH", "./data/budget.db").strip()
TIMEZONE = os.environ.get("TIMEZONE", "UTC").strip()
DAILY_CHECK_TIME = os.environ.get("DAILY_CHECK_TIME", "09:00").strip()
# When the opt-in "did you log anything today?" reminder goes out (see
# /remind and bot/jobs.py:send_daily_reminders). Deliberately separate from
# DAILY_CHECK_TIME (which processes recurring items/subscriptions, usually
# a morning job) since this one only makes sense in the evening.
REMINDER_TIME = os.environ.get("REMINDER_TIME", "20:00").strip()

DEFAULT_CATEGORIES = [
    # (name, is_necessity)
    ("Groceries", 1),
    ("Food & Drink", 0),
    ("Transport", 1),
    ("Housing & Bills", 1),
    ("Health", 1),
    ("Shopping", 0),
    ("Entertainment", 0),
    ("Subscriptions", 0),
    ("Travel", 0),
    ("Other", 0),
]

DEFAULT_THRESHOLD_PERCENTS = [80, 100]


def validate() -> list[str]:
    """Returns a list of human-readable problems with the current config."""
    problems = []
    if not BOT_TOKEN:
        problems.append(
            "TELEGRAM_BOT_TOKEN is not set. Get one from @BotFather and put it in your .env file."
        )
    db_dir = Path(DATABASE_PATH).expanduser().resolve().parent
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        problems.append(f"Can't create database directory {db_dir}: {exc}")
    return problems
