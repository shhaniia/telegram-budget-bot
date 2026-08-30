"""Tests the reworked /recent: today's-full-day view grouped by category
with a day total, tap-to-discard buttons that redraw the list in place
(rather than wiping it), the /recent yesterday|<date> argument, and the
currency-symbol description bug ("S$10 on shopping" -> a clean description).
Run with: python tests/test_recent.py
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

from bot import db, timeutil  # noqa: E402
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


def make_context(args=None):
    return SimpleNamespace(args=args or [], user_data={}, bot=SimpleNamespace(send_message=AsyncMock()))


def make_callback_update(data):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    return SimpleNamespace(
        message=None,
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
        callback_query=query,
    ), query


async def main():
    db.init_db()
    db.ensure_categories_seeded(USER_ID)

    # ---------------------------------------------------- currency-symbol bug
    food = db.get_category_by_name(USER_ID, "Food & Drink")
    shopping = db.get_category_by_name(USER_ID, "Shopping")

    u1, c1 = make_update(text="S$5 food for lunch"), make_context()
    await purchases.text_message(u1, c1)
    row1 = db.recent_purchases(USER_ID, limit=1)[0]
    check(
        "'S$5 food for lunch' parses to a clean description",
        row1["description"] == "food for lunch",
        repr(row1["description"]),
    )
    check("no stray '$' left in the description", "$" not in row1["description"])

    u2, c2 = make_update(text="$10 shopping today"), make_context()
    await purchases.text_message(u2, c2)
    row2 = db.recent_purchases(USER_ID, limit=1)[0]
    check(
        "'$10 shopping today' parses to a clean description",
        row2["description"] == "shopping today",
        repr(row2["description"]),
    )

    # -------------------------------------------------------------- /recent
    update = make_update()
    context = make_context()
    await purchases.recent_cmd(update, context)
    check("recent_cmd sends a reply", update.message.reply_text.await_count == 1)
    text = update.message.reply_text.await_args[0][0]
    markup = update.message.reply_text.await_args.kwargs.get("reply_markup")

    check("shows the day total ($15.00)", "$15.00" in text)
    check("groups by category — Food & Drink appears", "Food & Drink" in text)
    check("groups by category — Shopping appears", "Shopping" in text)
    check("no old 'Regret one? /undo' copy left in there", "Regret one" not in text)
    check("attaches a discard button grid instead of plain text instructions", markup is not None and len(markup.inline_keyboard) >= 1)

    all_buttons = [btn for row in markup.inline_keyboard for btn in row]
    check("one discard button per purchase logged today", len(all_buttons) == 2)
    check("discard buttons route through the recentundo callback", all(b.callback_data.startswith("recentundo:") for b in all_buttons))

    # ------------------------------------------------ tap-to-discard in place
    target_id = row2["id"]  # the shopping purchase
    cb_update, query = make_callback_update(f"recentundo:{target_id}")
    cb_context = make_context()
    await purchases.recent_undo_callback(cb_update, cb_context)
    check("discarding via the button actually deletes the purchase", db.get_purchase(target_id, USER_ID) is None)
    check("the message is redrawn in place (edit_message_text), not replaced with a bare 'Undone'", query.edit_message_text.await_count == 1)
    redrawn_text = query.edit_message_text.await_args[0][0]
    check("redrawn listing still shows the remaining purchase", "Food & Drink" in redrawn_text and "$5.00" in redrawn_text)
    check("redrawn listing no longer shows the discarded one", "Shopping" not in redrawn_text)
    redrawn_markup = query.edit_message_text.await_args.kwargs.get("reply_markup")
    check("redrawn listing's button grid shrank to just the remaining purchase", len(redrawn_markup.inline_keyboard) == 1)

    # discarding the last remaining purchase collapses to a clean message, no crash
    cb_update2, query2 = make_callback_update(f"recentundo:{row1['id']}")
    await purchases.recent_undo_callback(cb_update2, make_context())
    check("discarding the last item doesn't crash and confirms nothing's left", query2.edit_message_text.await_count == 1)
    final_text = query2.edit_message_text.await_args[0][0]
    check("final message reads as an 'all clear' rather than an empty list", "Nothing else logged" in final_text)

    # -------------------------------------------------------- empty-day path
    update_empty = make_update()
    context_empty = make_context(args=["2020-01-01"])
    await purchases.recent_cmd(update_empty, context_empty)
    check(
        "an explicit empty day says nothing was logged, not an error",
        "Nothing logged" in update_empty.message.reply_text.await_args[0][0],
    )

    # --------------------------------------------------------- bad date arg
    update_bad = make_update()
    context_bad = make_context(args=["not-a-date"])
    await purchases.recent_cmd(update_bad, context_bad)
    check(
        "a garbage date argument gets a helpful usage message, not a crash",
        "doesn't look right" in update_bad.message.reply_text.await_args[0][0],
    )

    # --------------------------------------------------------- yesterday arg
    yesterday_iso = timeutil.yesterday().isoformat()
    db.add_purchase(USER_ID, 20, food["id"], "yesterday's dinner", "manual", purchase_date=yesterday_iso)
    update_y = make_update()
    context_y = make_context(args=["yesterday"])
    await purchases.recent_cmd(update_y, context_y)
    y_text = update_y.message.reply_text.await_args[0][0]
    check("'/recent yesterday' finds yesterday's purchase", "yesterday's dinner" in y_text and "Yesterday" in y_text)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All /recent checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
