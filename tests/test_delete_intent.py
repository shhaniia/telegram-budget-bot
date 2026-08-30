"""Tests the natural-language 'delete a purchase' intent — the exact bug
reported: "remove the $5 for lunch" / "discard $5 lunch log" were being
treated as brand-new purchases to categorize, instead of deleting the
existing $5 lunch entry. Run with: python tests/test_delete_intent.py
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

from bot import db  # noqa: E402
from bot.handlers import purchases  # noqa: E402

FAILURES = []
USER_ID = 111111


def check(label, condition, extra=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {extra}" if extra and not condition else ""))
    if not condition:
        FAILURES.append(label)


def make_update(text=None):
    message = SimpleNamespace(reply_text=AsyncMock(), text=text)
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )


def make_context():
    return SimpleNamespace(args=[], user_data={}, bot=SimpleNamespace(send_message=AsyncMock()))


async def main():
    db.init_db()
    db.ensure_categories_seeded(USER_ID)
    food = db.get_category_by_name(USER_ID, "Food & Drink")
    shopping = db.get_category_by_name(USER_ID, "Shopping")

    # --------------------------------------------------------- the actual bug
    lunch_id = db.add_purchase(USER_ID, 5.00, food["id"], "for lunch", "manual")
    shopping_id = db.add_purchase(USER_ID, 16.00, shopping["id"], "for shopping", "manual")
    # (mirrors the screenshot: a $5 Food&Drink 'for lunch' entry and a $16 Shopping entry already logged)

    before_count = len(db.recent_purchases(USER_ID, limit=50))
    update = make_update(text="remove the $5 for lunch")
    context = make_context()
    await purchases.text_message(update, context)

    check(
        "'remove the $5 for lunch' does NOT create a new pending purchase",
        len(db.recent_purchases(USER_ID, limit=50)) < before_count + 1,
    )
    check(
        "it actually deletes the matching $5 purchase",
        db.get_purchase(lunch_id, USER_ID) is None,
    )
    check(
        "the reply confirms a deletion, not 'which category does this go in'",
        "category" not in update.message.reply_text.await_args[0][0].lower(),
    )
    check(
        "the $16 shopping entry is untouched",
        db.get_purchase(shopping_id, USER_ID) is not None,
    )

    # ------------------------------------------------- second phrasing from the report
    lunch_id2 = db.add_purchase(USER_ID, 5.00, food["id"], "lunch log", "manual")
    update2 = make_update(text="discard $5 lunch log")
    context2 = make_context()
    await purchases.text_message(update2, context2)
    check(
        "'discard $5 lunch log' deletes the matching purchase",
        db.get_purchase(lunch_id2, USER_ID) is None,
    )
    check(
        "reply does not ask for a category",
        "category" not in update2.message.reply_text.await_args[0][0].lower(),
    )

    # --------------------------------------------------------------- ambiguous
    id_a = db.add_purchase(USER_ID, 8.00, food["id"], "coffee", "manual")
    id_b = db.add_purchase(USER_ID, 8.00, shopping["id"], "coffee mug", "manual")
    update3 = make_update(text="delete the $8 coffee")
    context3 = make_context()
    await purchases.text_message(update3, context3)
    check(
        "two equally-good matches -> nothing deleted yet",
        db.get_purchase(id_a, USER_ID) is not None and db.get_purchase(id_b, USER_ID) is not None,
    )
    markup = update3.message.reply_text.await_args.kwargs.get("reply_markup")
    check("ambiguous match offers a picker instead of guessing", markup is not None and len(markup.inline_keyboard) == 2)

    # tapping one of the picker buttons deletes exactly that one (reuses the undo callback)
    button = markup.inline_keyboard[0][0]
    check("picker buttons route through the existing undo callback", button.callback_data.startswith("undo:"))

    # ------------------------------------------------------------------ no match
    update4 = make_update(text="remove the $999 yacht purchase")
    context4 = make_context()
    await purchases.text_message(update4, context4)
    check(
        "no match found -> a clear 'couldn't find that' reply, not a crash",
        "couldn't find" in update4.message.reply_text.await_args[0][0].lower()
        or "nothing matched" in update4.message.reply_text.await_args[0][0].lower(),
    )

    # ------------------------------------------------------ too vague to act on
    update5 = make_update(text="undo")
    context5 = make_context()
    await purchases.text_message(update5, context5)
    reply5 = update5.message.reply_text.await_args[0][0]
    check(
        "a bare 'undo' with no amount/keyword asks for detail instead of listing everything",
        "which purchase" in reply5.lower(),
    )

    # ---------------------------------------------------------------- most recent
    latest_id = db.add_purchase(USER_ID, 3.30, food["id"], "kopi", "manual")
    update6 = make_update(text="delete my last purchase")
    context6 = make_context()
    await purchases.text_message(update6, context6)
    check("'delete my last purchase' removes the most recently logged one", db.get_purchase(latest_id, USER_ID) is None)

    # --------------------------------------------- ordinary purchase logs unaffected
    before = len(db.recent_purchases(USER_ID, limit=50))
    update7 = make_update(text="12.50 groceries milk and bread")
    context7 = make_context()
    await purchases.text_message(update7, context7)
    check(
        "an ordinary purchase log still logs normally (delete-intent doesn't over-trigger)",
        len(db.recent_purchases(USER_ID, limit=50)) == before + 1,
    )

    # -------------------------------------------------- help-style question unaffected
    update8 = make_update(text="how do I discard a wrong entry")
    context8 = make_context()
    await purchases.text_message(update8, context8)
    reply8 = update8.message.reply_text.await_args[0][0]
    check(
        "a 'how do I...' question is not treated as an actual delete request",
        "which purchase" not in reply8.lower() and "couldn't find" not in reply8.lower(),
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All delete-intent checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
