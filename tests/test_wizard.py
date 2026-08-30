"""Tests the conversational wizard flows, the discard/undo buttons, and the
new bar-chart summary — all with mocked Telegram objects, no live bot needed.
Run with: python tests/test_wizard.py
"""
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

from bot import db, wizard  # noqa: E402
from bot.handlers import purchases, reports  # noqa: E402

FAILURES = []
USER_ID = 111111


def check(label, condition, extra=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {extra}" if extra and not condition else ""))
    if not condition:
        FAILURES.append(label)


def make_context():
    return SimpleNamespace(user_data={}, bot=SimpleNamespace(send_message=AsyncMock()), args=[])


def make_text_update(text):
    message = SimpleNamespace(reply_text=AsyncMock(), text=text)
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
        callback_query=None,
    )


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

    # ---------------------------------------------------------- addcategory
    context = make_context()
    u = make_text_update("/addcategory")
    await wizard.start(u, context, "addcategory")
    check("wizard active after start", wizard.is_active(context))
    check("first prompt sent", context.bot.send_message.await_count == 1)

    u2 = make_text_update("Skiing Fund")
    await wizard.handle_text(u2, context)
    check("name captured, still active for necessity step", wizard.is_active(context))
    check("necessity prompt sent", context.bot.send_message.await_count == 2)

    step = context.user_data["wizard"]["step"]
    cb_update, query = make_callback_update(f"wiz:{step}:necessity")
    await wizard.handle_callback(cb_update, context)
    check("wizard finished after last field", not wizard.is_active(context))
    cat = db.get_category_by_name(USER_ID, "Skiing Fund")
    check("category actually created via wizard", cat is not None and cat["is_necessity"] == 1)

    # -------------------------------------------------------------- /cancel
    context2 = make_context()
    u3 = make_text_update("/addcategory")
    await wizard.start(u3, context2, "addcategory")
    check("second wizard active", wizard.is_active(context2))
    cancel_update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    await wizard.cancel_cmd(cancel_update, context2)
    check("wizard cleared after /cancel", not wizard.is_active(context2))
    check("nothing created for cancelled flow", db.get_category_by_name(USER_ID, "Should Not Exist") is None)

    # --------------------------------------------------- setbudget wizard
    context3 = make_context()
    u4 = make_text_update("/setbudget")
    await wizard.start(u4, context3, "setbudget")
    food = db.get_category_by_name(USER_ID, "Food & Drink")
    cb1, _ = make_callback_update(f"wiz:0:{food['id']}")
    await wizard.handle_callback(cb1, context3)
    u5 = make_text_update("275")
    await wizard.handle_text(u5, context3)
    step3 = context3.user_data["wizard"]["step"]
    cb2, _ = make_callback_update(f"wiz:{step3}:weekly")
    await wizard.handle_callback(cb2, context3)
    check("setbudget wizard finished", not wizard.is_active(context3))
    budget = db.get_budget(USER_ID, food["id"], "weekly")
    check("budget set via wizard", budget is not None and budget["amount"] == 275)

    # bad amount mid-wizard should re-prompt, not crash or advance
    context4 = make_context()
    u6 = make_text_update("/setbudget")
    await wizard.start(u6, context4, "setbudget")
    cb3, _ = make_callback_update(f"wiz:0:{food['id']}")
    await wizard.handle_callback(cb3, context4)
    u7 = make_text_update("not a number")
    await wizard.handle_text(u7, context4)
    check("invalid amount keeps wizard on same step", context4.user_data["wizard"]["step"] == 1)

    # ------------------------------------------ discard / undo everywhere
    purchase_id = db.add_purchase(USER_ID, 42.00, food["id"], "test entry", "manual")
    undo_update, undo_query = make_callback_update(f"undo:{purchase_id}")
    await purchases.undo_callback(undo_update, make_context())
    check("undo callback deletes the purchase", db.get_purchase(purchase_id, USER_ID) is None)
    check("undo confirms via edit_message_text", undo_query.edit_message_text.await_count == 1)

    # wrong-category two-step: open picker, then reassign
    purchase_id2 = db.add_purchase(USER_ID, 15.00, food["id"], "misfiled", "manual")
    open_update, open_query = make_callback_update(f"recatopen:{purchase_id2}")
    await purchases.recategorize_open_callback(open_update, make_context())
    check("opening the picker edits the keyboard, not the text", open_query.edit_message_reply_markup.await_count == 1)

    groceries = db.get_category_by_name(USER_ID, "Groceries")
    recat_update, recat_query = make_callback_update(f"recat:{purchase_id2}:{groceries['id']}")
    await purchases.recategorize_callback(recat_update, make_context())
    moved = db.get_purchase(purchase_id2, USER_ID)
    check("recategorize actually moved the purchase", moved is not None and moved["category_id"] == groceries["id"])

    # ------------------------------------------------------- bar chart
    db.add_purchase(USER_ID, 100, groceries["id"], "big shop", "manual")
    summary_reply = SimpleNamespace(reply_text=AsyncMock())
    summary_update = SimpleNamespace(message=summary_reply, effective_user=SimpleNamespace(id=USER_ID))
    summary_context = SimpleNamespace(args=[])
    await reports.summary_cmd(summary_update, summary_context)
    sent_text = summary_reply.reply_text.await_args[0][0]
    check("summary includes a code-block bar chart", "```" in sent_text and "█" in sent_text)
    check("summary includes MTD header", "breakdown" in sent_text.lower())

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All wizard/discard/summary checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
