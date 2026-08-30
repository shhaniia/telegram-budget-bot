"""Exercises the actual command handler functions (not just the underlying
db/regex logic) using lightweight mocks for python-telegram-bot's Update /
Context objects — no network or real bot token needed.
Run with: python tests/test_handlers.py
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

from bot import db  # noqa: E402
from bot.handlers import budgets, recurring, subscriptions, purchases, categories  # noqa: E402

FAILURES = []
USER_ID = 111111


def check(label, condition, extra=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {extra}" if extra and not condition else ""))
    if not condition:
        FAILURES.append(label)


def make_update(args=None, text=None):
    message = SimpleNamespace(reply_text=AsyncMock(), text=text)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    context = SimpleNamespace(args=args or [], user_data={}, bot=SimpleNamespace(send_message=AsyncMock()))
    return update, context, message.reply_text


def last_reply_text(mock: AsyncMock) -> str:
    assert mock.await_args is not None, "reply_text was never called"
    args, kwargs = mock.await_args
    return args[0] if args else kwargs.get("text", "")


async def main():
    db.init_db()
    db.ensure_categories_seeded(USER_ID)

    # /addcategory
    u, c, reply = make_update(args=["Pets", "necessity"])
    await categories.add_category_cmd(u, c)
    check("addcategory creates category", db.get_category_by_name(USER_ID, "Pets") is not None, last_reply_text(reply))
    check("addcategory respects necessity flag", db.get_category_by_name(USER_ID, "Pets")["is_necessity"] == 1)

    # /setbudget with multi-word category + explicit period
    u, c, reply = make_update(args=["Food", "&", "Drink", "250", "weekly"])
    await budgets.set_budget_cmd(u, c)
    food = db.get_category_by_name(USER_ID, "Food & Drink")
    budget = db.get_budget(USER_ID, food["id"], "weekly")
    check("setbudget parses multi-word category + period", budget is not None and budget["amount"] == 250, last_reply_text(reply))

    # /setthreshold global default
    u, c, reply = make_update(args=["85"])
    await budgets.set_threshold_cmd(u, c)
    check("setthreshold global default saved", 85 in db.thresholds_for_category(USER_ID, food["id"]), last_reply_text(reply))

    # /addrecurring with multi-word name + necessity flag
    u, c, reply = make_update(args=["Home", "Insurance", "45.00", "Housing", "&", "Bills", "yearly", "necessity"])
    await recurring.add_recurring_cmd(u, c)
    rec = db.get_recurring_by_name(USER_ID, "Home Insurance")
    check(
        "addrecurring parses multi-word name/category + frequency + necessity",
        rec is not None and rec["frequency"] == "yearly" and rec["is_necessity"] == 1 and abs(rec["amount"] - 45.00) < 0.001,
        last_reply_text(reply),
    )

    # /addsub full form
    u, c, reply = make_update(args=["Spotify", "Family", "16.99", "2026-09-10", "monthly", "5"])
    await subscriptions.add_sub_cmd(u, c)
    sub = db.get_subscription_by_name(USER_ID, "Spotify Family")
    check(
        "addsub parses multi-word name + amount + date + frequency + reminder_days",
        sub is not None and sub["reminder_days_before"] == 5 and sub["next_billing_date"] == "2026-09-10",
        last_reply_text(reply),
    )

    # /addsub minimal form (defaults)
    u, c, reply = make_update(args=["Netflix", "15.99", "2026-09-05"])
    await subscriptions.add_sub_cmd(u, c)
    sub2 = db.get_subscription_by_name(USER_ID, "Netflix")
    check("addsub defaults frequency=monthly, reminder=3", sub2 is not None and sub2["frequency"] == "monthly" and sub2["reminder_days_before"] == 3, last_reply_text(reply))

    # plain-text quick log with recognized category -> immediate save
    u, c, reply = make_update(text="9.90 groceries milk and bread")
    await purchases.text_message(u, c)
    recent = db.recent_purchases(USER_ID, limit=1)
    check("free-text log auto-detects category and saves", recent[0]["amount"] == 9.90 and recent[0]["category_name"] == "Groceries", last_reply_text(reply))

    # plain-text quick log with NO recognized category -> pending confirmation, not yet saved
    before_count = len(db.recent_purchases(USER_ID, limit=50))
    u, c, reply = make_update(text="7.25 xyzzy unmatched thing")
    await purchases.text_message(u, c)
    after_count = len(db.recent_purchases(USER_ID, limit=50))
    check("unmatched category doesn't save yet, asks for category", after_count == before_count, last_reply_text(reply))

    # /budgets renders without exploding
    u, c, reply = make_update(args=[])
    await budgets.budgets_cmd(u, c)
    check("budgets_cmd runs without error", reply.await_args is not None)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All handler checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
