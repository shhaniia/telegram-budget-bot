"""Tests /whatsnext — the personalized "what's next" suggestion engine:
each rule (brand-new user, missing budget, missing threshold, repeat
purchase worth automating, subscription hiding in manual purchases,
reminder-off after a gap, pace-nudge carryover, all-caught-up fallback),
plus the action buttons that let a suggestion actually be acted on.
Run with: python tests/test_suggestions.py
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

from bot import checkins, db, suggestions, timeutil, wizard  # noqa: E402

FAILURES = []
USER_ID = 111111


def check(label, condition, extra=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {extra}" if extra and not condition else ""))
    if not condition:
        FAILURES.append(label)


def make_update():
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )


def make_context():
    return SimpleNamespace(args=[], user_data={}, bot=SimpleNamespace(send_message=AsyncMock()))


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

    # ------------------------------------------------------------ brand new
    fresh_id = 900001
    suggestions_list = suggestions.build_suggestions(fresh_id)
    check("brand-new user gets exactly one onboarding suggestion", len(suggestions_list) == 1)
    check(
        "onboarding suggestion offers a setbudget button",
        suggestions_list[0]["callback_data"] == "wnaction:setbudget:none",
    )

    # ------------------------------------------------------- real user setup
    db.ensure_categories_seeded(USER_ID)
    food = db.get_category_by_name(USER_ID, "Food & Drink")
    groceries = db.get_category_by_name(USER_ID, "Groceries")
    sub_cat = db.get_category_by_name(USER_ID, "Subscriptions")

    db.add_purchase(USER_ID, 20, food["id"], "dinner", "manual")

    # -------------------------------------------------- no budgets at all
    result = suggestions.build_suggestions(USER_ID)
    check("no-budgets-yet suggestion appears when there's spend and zero budgets", any("no budget set" in s["text"] for s in result))
    budget_suggestion = next(s for s in result if "no budget set" in s["text"])
    check("it points a button at the top-spending category", budget_suggestion["callback_data"] == f"wnaction:setbudget:{food['id']}")

    # -------------------------------------------- unbudgeted top category
    db.set_budget(USER_ID, food["id"], 500, "monthly")
    db.add_purchase(USER_ID, 30, groceries["id"], "groceries run", "manual")
    result2 = suggestions.build_suggestions(USER_ID)
    check(
        "once Food & Drink has a budget, Groceries (unbudgeted, $30 spent) gets suggested instead",
        any("Groceries" in s["text"] and "no budget" in s["text"] for s in result2),
    )

    # ------------------------------------------------------ missing threshold
    check("threshold suggestion appears once a budget exists but no threshold set", any("alert threshold" in s["text"] for s in result2))

    db.set_threshold(USER_ID, 85, food["id"])
    result3 = suggestions.build_suggestions(USER_ID)
    check("threshold suggestion disappears once a threshold is set", not any("alert threshold" in s["text"] for s in result3))

    # --------------------------------------------------------- recurring candidate
    for _ in range(4):
        db.add_purchase(USER_ID, 5, groceries["id"], "top-up", "manual")
    result4 = suggestions.build_suggestions(USER_ID, limit=10)
    check(
        "4+ purchases in an un-automated category suggests making it recurring",
        any("recurring item" in s["text"] and "Groceries" in s["text"] for s in result4),
    )

    db.add_recurring(USER_ID, "Groceries top-up", 5, groceries["id"], "weekly", True, timeutil.today_iso())
    result5 = suggestions.build_suggestions(USER_ID, limit=10)
    check(
        "recurring suggestion disappears once that category actually has a recurring item",
        not any("recurring item" in s["text"] and "Groceries" in s["text"] for s in result5),
    )

    # ------------------------------------------------------- subscription candidate
    db.add_purchase(USER_ID, 15.99, sub_cat["id"], "Netflix again", "manual")
    result6 = suggestions.build_suggestions(USER_ID, limit=10)
    check("manual spend under Subscriptions with none tracked suggests /addsub", any("addsub" in s["text"] or "as one" in s["text"] for s in result6))

    db.add_subscription(USER_ID, "Netflix", 15.99, timeutil.today_iso(), "monthly", 3)
    result7 = suggestions.build_suggestions(USER_ID, limit=10)
    check("subscription suggestion disappears once one's actually tracked", not any("Track a subscription" == s.get("button_label") for s in result7))

    # ---------------------------------------------------------- pace nudge carryover
    db.set_budget(USER_ID, food["id"], 50, "monthly")  # tight budget so spend reads as way over pace
    for d in [timeutil.today().replace(day=15)]:
        checkins.build_report(USER_ID, d, checkin_type="midmonth", persist=True)
    # push the streak past the nudge threshold across a few simulated months
    import datetime as _dt

    for month_offset in range(1, checkins._STREAK_TO_NUDGE):
        d = _dt.date(2027, ((15 + month_offset - 1) % 12) + 1, 15)
        checkins.build_report(USER_ID, d, checkin_type="midmonth", persist=True)
    pace_rows = db.nudge_worthy_pace_statuses(USER_ID, checkins._STREAK_TO_NUDGE)
    check("test setup actually produced a nudge-worthy pace streak", len(pace_rows) >= 1)

    result8 = suggestions.build_suggestions(USER_ID, limit=10)
    check("a nudge-worthy pace streak surfaces in /whatsnext too", any(s["callback_data"] and s["callback_data"].startswith("checkinbudget:") for s in result8))

    # ------------------------------------------------------------- reminder off
    db.add_purchase(USER_ID, 4, food["id"], "kopi", "manual")
    db.add_purchase(USER_ID, 4, food["id"], "kopi", "manual")
    db.add_purchase(USER_ID, 4, food["id"], "kopi", "manual")
    # (reminder stays off, yesterday has nothing logged by construction of this fresh temp db)
    result9 = suggestions.build_suggestions(USER_ID, limit=10)
    check("reminder-off + a logging gap suggests turning reminders on", any(s.get("callback_data") == "wnaction:remind:none" for s in result9))

    # -------------------------------------------------------------- limit respected
    small = suggestions.build_suggestions(USER_ID, limit=1)
    check("limit parameter is respected", len(small) == 1)

    # ---------------------------------------------------------------- /whatsnext cmd
    update = make_update()
    context = make_context()
    from bot import suggestions as suggestions_mod

    await suggestions_mod.whatsnext_cmd(update, context)
    check("whatsnext_cmd sends a reply", update.message.reply_text.await_count == 1)
    text = update.message.reply_text.await_args[0][0]
    check("reply includes the header", "next" in text.lower() or "stands out" in text.lower())
    markup = update.message.reply_text.await_args.kwargs.get("reply_markup")
    check("reply attaches at least one action button", markup is not None and len(markup.inline_keyboard) >= 1)

    # ------------------------------------------------- action callback: setbudget prefill
    cb_update, query = make_callback_update(f"wnaction:setbudget:{groceries['id']}")
    cb_context = make_context()
    await suggestions_mod.whatsnext_action_callback(cb_update, cb_context)
    check("tapping a setbudget suggestion starts the wizard", wizard.is_active(cb_context))
    state = cb_context.user_data["wizard"]
    check("wizard is pre-filled with the suggested category", state["data"].get("category_id") == groceries["id"])
    check("wizard skips straight to the amount step", state["step"] == 1)

    # ------------------------------------------------- action callback: setthreshold (no prefill)
    cb_update2, query2 = make_callback_update("wnaction:setthreshold:none")
    cb_context2 = make_context()
    await suggestions_mod.whatsnext_action_callback(cb_update2, cb_context2)
    check("tapping a setthreshold suggestion starts that wizard fresh", wizard.is_active(cb_context2))
    check("setthreshold wizard starts at step 0 (percent)", cb_context2.user_data["wizard"]["step"] == 0)

    # ------------------------------------------------- action callback: remind toggle
    db.set_daily_reminder(USER_ID, False)
    cb_update3, query3 = make_callback_update("wnaction:remind:none")
    cb_context3 = make_context()
    await suggestions_mod.whatsnext_action_callback(cb_update3, cb_context3)
    check("tapping the reminder suggestion turns reminders on", db.get_daily_reminder_enabled(USER_ID) is True)
    check("it confirms via a new message rather than crashing", cb_context3.bot.send_message.await_count == 1)

    # ------------------------------------------------- all caught up fallback
    caught_up_user = 900002
    db.ensure_categories_seeded(caught_up_user)
    cu_food = db.get_category_by_name(caught_up_user, "Food & Drink")
    db.add_purchase(caught_up_user, 10, cu_food["id"], "lunch", "manual")
    db.set_budget(caught_up_user, cu_food["id"], 1000, "monthly")  # generously under pace, no threshold needed for this check
    db.set_threshold(caught_up_user, 90, cu_food["id"])
    result_caught_up = suggestions.build_suggestions(caught_up_user)
    check(
        "a user with nothing actionable gets the all-caught-up message, not an empty list",
        len(result_caught_up) == 1 and result_caught_up[0]["button_label"] is None,
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All /whatsnext checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
