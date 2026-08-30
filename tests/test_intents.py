"""Tests the natural-language intent router: representative phrasings must
route to the right command, and — just as importantly — ordinary purchase
logs ("12.50 lunch") must NOT get hijacked by it.
Run with: python tests/test_intents.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0000000000:TEST_TOKEN_NOT_REAL")
os.environ.setdefault("ALLOWED_USER_IDS", "111111")
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ.setdefault("TIMEZONE", "Asia/Singapore")
os.environ.setdefault("DAILY_CHECK_TIME", "09:00")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import db, intents  # noqa: E402
from bot.handlers import purchases  # noqa: E402

FAILURES = []
USER_ID = 111111


def check(label, condition, extra=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {extra}" if extra and not condition else ""))
    if not condition:
        FAILURES.append(label)


def make_update(text):
    message = SimpleNamespace(reply_text=AsyncMock(), text=text)
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )


def make_context():
    return SimpleNamespace(args=[], user_data={}, bot=SimpleNamespace(send_message=AsyncMock()))


async def _matched_command(text: str) -> str | None:
    """Returns the command string the router picked for `text`, or None."""
    update = make_update(text)
    context = make_context()
    matched = {}

    async def fake_hinted(u, c, command, runner):
        matched["command"] = command

    async def fake_hinted_wizard(u, c, command, flow):
        matched["command"] = command

    async def fake_hinted_checkin(u, c, command):
        matched["command"] = command

    async def fake_hinted_whatsnext(u, c, command):
        matched["command"] = command

    async def fake_direct(u, c, runner):
        matched["command"] = "delete a purchase"

    originals = (
        intents._hinted,
        intents._hinted_wizard,
        intents._hinted_checkin,
        intents._hinted_whatsnext,
        intents._direct,
    )
    (
        intents._hinted,
        intents._hinted_wizard,
        intents._hinted_checkin,
        intents._hinted_whatsnext,
        intents._direct,
    ) = (fake_hinted, fake_hinted_wizard, fake_hinted_checkin, fake_hinted_whatsnext, fake_direct)
    try:
        await intents.try_route(update, context)
    finally:
        (
            intents._hinted,
            intents._hinted_wizard,
            intents._hinted_checkin,
            intents._hinted_whatsnext,
            intents._direct,
        ) = originals
    return matched.get("command")


async def main():
    db.init_db()
    db.ensure_categories_seeded(USER_ID)

    cases = [
        ("what did i spend today", "/recent"),
        ("show me my recent purchases", "/recent"),
        ("how much did i spend this week", "/summary week"),
        ("how much have i spent this year", "/summary year"),
        ("give me a spending breakdown", "/summary"),
        ("how am i doing this month", "/summary"),
        ("what's my budget status", "/budgets"),
        ("how much do i have left", "/budgets"),
        ("am i on track", "/checkin"),
        ("what's next", "/whatsnext"),
        ("what should i do", "/whatsnext"),
        ("any suggestions", "/whatsnext"),
        ("what are my options", "/whatsnext"),
        ("what categories do i have", "/categories"),
        ("i want to add a category", "/addcategory"),
        ("set a budget for food", "/setbudget"),
        ("i want to update my budget", "/setbudget"),
        ("set an alert threshold", "/setthreshold"),
        ("what recurring purchases do i have", "/recurring"),
        ("add a recurring purchase", "/addrecurring"),
        ("what subscriptions do i have", "/subs"),
        ("add a subscription", "/addsub"),
        ("remind me to log my spending", "/remind on"),
        ("turn off reminders please", "/remind off"),
        ("help", "/help"),
        ("what can you do", "/help"),
    ]
    for text, expected in cases:
        got = await _matched_command(text)
        check(f"'{text}' -> {expected}", got == expected, f"got {got!r}")

    # ordinary purchase logs must NOT be hijacked by intent routing
    non_matches = ["12.50 lunch", "60 taxi to work", "3.20 coffee this morning", "Rent 1500"]
    for text in non_matches:
        got = await _matched_command(text)
        check(f"'{text}' is not treated as an intent", got is None, f"got {got!r}")

    # end-to-end: text_message actually logs a plain purchase when no intent matches
    update = make_update("9.90 groceries milk and bread")
    context = make_context()
    await purchases.text_message(update, context)
    recent = db.recent_purchases(USER_ID, limit=1)
    check(
        "text_message still logs an ordinary purchase after intent routing was added",
        recent[0]["amount"] == 9.90 and recent[0]["category_name"] == "Groceries",
    )

    # end-to-end: a natural-language question actually runs the real handler
    update2 = make_update("what did i spend today")
    context2 = make_context()
    await purchases.text_message(update2, context2)
    check(
        "natural-language routing sends at least the hint + the real command's reply",
        update2.message.reply_text.await_count >= 2,
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All intent-routing checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
