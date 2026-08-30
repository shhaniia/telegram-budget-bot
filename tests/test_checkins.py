"""Tests the mid-month / T-3-EOM budget pace check-ins: pace classification,
the multi-check-in streak that triggers a "update your budget?" nudge, the
callback that jumps into the setbudget wizard pre-filled, and the opt-in
daily reminder settings. Run with: python tests/test_checkins.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0000000000:TEST_TOKEN_NOT_REAL")
os.environ.setdefault("ALLOWED_USER_IDS", "111111")
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ.setdefault("TIMEZONE", "Asia/Singapore")
os.environ.setdefault("DAILY_CHECK_TIME", "09:00")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import checkins, db, wizard  # noqa: E402
from bot.handlers import settings  # noqa: E402

FAILURES = []
USER_ID = 111111


def check(label, condition, extra=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {extra}" if extra and not condition else ""))
    if not condition:
        FAILURES.append(label)


def make_context():
    return SimpleNamespace(user_data={}, bot=SimpleNamespace(send_message=AsyncMock()), args=[])


def make_update(text=None, args=None):
    message = SimpleNamespace(reply_text=AsyncMock(), text=text)
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
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

    # ------------------------------------------------------------ pace math
    check("15 days in, 50% used -> within range", checkins._classify(50, 50) == "within")
    check("15 days in, 90% used -> over pace", checkins._classify(90, 50) == "over")
    check("15 days in, 10% used -> under pace", checkins._classify(10, 50) == "under")
    check("exactly at the over threshold counts as over", checkins._classify(57.5, 50) == "over")
    check("just inside the buffer still counts as within", checkins._classify(55, 50) == "within")

    check("day 15 is a mid-month check-in", checkins.checkin_type_for(date(2026, 8, 15)) == "midmonth")
    check("3 days before Aug 31 (28th) is an eom3 check-in", checkins.checkin_type_for(date(2026, 8, 28)) == "eom3")
    check("day 20 is not a check-in day", checkins.checkin_type_for(date(2026, 8, 20)) is None)
    check("Feb (28 days, non-leap 2026) eom3 lands on the 25th", checkins.checkin_type_for(date(2026, 2, 25)) == "eom3")

    # -------------------------------------------------- build_report basics
    food = db.get_category_by_name(USER_ID, "Food & Drink")
    db.set_budget(USER_ID, food["id"], 100, "monthly")
    db.add_purchase(USER_ID, 90, food["id"], "overspending", "manual")  # 90% used

    text, nudge_worthy = checkins.build_report(USER_ID, date(2026, 8, 15), checkin_type="midmonth", persist=True)
    check("report mentions the category", "Food & Drink" in text)
    check("report shows the running-hot tag for an over-pace category", "running hot" in text)
    check("no nudge yet after a single check-in", nudge_worthy == [])

    status_row = db.get_pace_status(USER_ID, food["id"])
    check("pace status persisted after a persist=True run", status_row is not None and status_row["status"] == "over")
    check("streak starts at 1", status_row["streak"] == 1)

    # re-running the SAME check-in (same key) must not double the streak
    text2, _ = checkins.build_report(USER_ID, date(2026, 8, 15), checkin_type="midmonth", persist=True)
    status_row2 = db.get_pace_status(USER_ID, food["id"])
    check("re-running the same check-in doesn't advance the streak", status_row2["streak"] == 1)

    # -------------------------------------------- streak crosses the nudge threshold
    # Simulate 3 more consecutive "over" check-ins (different months so each
    # one has a distinct checkin_key) -> streak should reach 4 and nudge.
    for i, d in enumerate([date(2026, 9, 15), date(2026, 10, 15), date(2026, 11, 15)], start=2):
        text_n, nudge_n = checkins.build_report(USER_ID, d, checkin_type="midmonth", persist=True)
        status_row_n = db.get_pace_status(USER_ID, food["id"])
        check(f"streak reaches {i} after check-in #{i}", status_row_n["streak"] == i)
        if i < 4:
            check(f"no nudge yet at streak {i}", nudge_n == [])
        else:
            check(
                "nudge fires once streak passes the 'more than 3' threshold",
                len(nudge_n) == 1 and nudge_n[0]["category_id"] == food["id"],
            )

    # a status change resets the streak, even for a category with a streak going
    transport = db.get_category_by_name(USER_ID, "Transport")
    db.set_budget(USER_ID, transport["id"], 100, "monthly")
    db.add_purchase(USER_ID, 90, transport["id"], "over pace", "manual")  # 90% used, same shape as Food
    checkins.build_report(USER_ID, date(2026, 8, 15), checkin_type="midmonth", persist=True)
    checkins.build_report(USER_ID, date(2026, 9, 15), checkin_type="midmonth", persist=True)
    before = db.get_pace_status(USER_ID, transport["id"])
    check("transport built up a 2-check-in 'over' streak", before["status"] == "over" and before["streak"] == 2)

    # loosen the budget a lot -> same spend now reads as comfortably under pace
    db.set_budget(USER_ID, transport["id"], 1000, "monthly")
    checkins.build_report(USER_ID, date(2026, 10, 15), checkin_type="midmonth", persist=True)
    after = db.get_pace_status(USER_ID, transport["id"])
    check("status actually flipped to under after loosening the budget", after["status"] == "under")
    check("streak resets to 1 when the status changes", after["streak"] == 1)

    # read-only /checkin-style call reflects the current under-pace status without writing
    text3, _ = checkins.build_report(USER_ID, date(2026, 8, 15), checkin_type="midmonth", persist=False)
    check("read-only call classifies transport as comfortably under", "Transport" in text3 and "comfortably under" in text3)
    after_readonly = db.get_pace_status(USER_ID, transport["id"])
    check("a read-only (persist=False) call never touches the stored streak", after_readonly["streak"] == 1)

    # -------------------------------------------------------------- /checkin cmd
    update = make_update()
    context = make_context()
    await checkins.checkin_cmd(update, context)
    check("checkin_cmd replies with a report", update.message.reply_text.await_count == 1)
    sent = update.message.reply_text.await_args[0][0]
    check("checkin_cmd's report mentions the month-elapsed line", "month gone" in sent)

    # ----------------------------------------------- checkin_budget_callback
    cb_update, query = make_callback_update(f"checkinbudget:{food['id']}")
    cb_context = make_context()
    await checkins.checkin_budget_callback(cb_update, cb_context)
    check("tapping the nudge button starts the setbudget wizard", wizard.is_active(cb_context))
    state = cb_context.user_data["wizard"]
    check("wizard pre-fills the category from the nudge", state["data"].get("category_id") == food["id"])
    check("wizard skips straight past the category step", state["step"] == 1)  # 0=category, so next is amount

    # -------------------------------------------------------- daily reminders
    check("reminder off by default", db.get_daily_reminder_enabled(USER_ID) is False)

    remind_update = make_update()
    remind_context = make_context()
    remind_context.args = []
    await settings.remind_cmd(remind_update, remind_context)
    check("bare /remind reports current (off) status", "off" in remind_update.message.reply_text.await_args[0][0])

    remind_update2 = make_update()
    remind_context2 = make_context()
    remind_context2.args = ["on"]
    await settings.remind_cmd(remind_update2, remind_context2)
    check("/remind on enables it", db.get_daily_reminder_enabled(USER_ID) is True)
    check("user shows up in the enabled-reminders list", USER_ID in db.list_users_with_reminder_enabled())

    remind_update3 = make_update()
    remind_context3 = make_context()
    remind_context3.args = ["off"]
    await settings.remind_cmd(remind_update3, remind_context3)
    check("/remind off disables it again", db.get_daily_reminder_enabled(USER_ID) is False)
    check("user drops out of the enabled-reminders list", USER_ID not in db.list_users_with_reminder_enabled())

    import bot.timeutil as timeutil

    check("no purchases recorded for a made-up future date", db.purchase_count_for_date(USER_ID, "2099-01-01") == 0)
    db.add_purchase(USER_ID, 4.5, food["id"], "kopi", "manual", purchase_date=timeutil.today_iso())
    check(
        "purchase_count_for_date sees today's purchases",
        db.purchase_count_for_date(USER_ID, timeutil.today_iso()) > 0,
    )

    from bot import jobs

    db.set_daily_reminder(USER_ID, True)
    job_context = make_context()
    await jobs.send_daily_reminders(job_context)
    check(
        "no reminder sent to a user who already logged something today",
        job_context.bot.send_message.await_count == 0,
    )

    # a second user who's enabled reminders but hasn't logged anything today gets nudged
    other_user = 222222
    db.ensure_categories_seeded(other_user)
    db.set_daily_reminder(other_user, True)
    job_context2 = make_context()
    await jobs.send_daily_reminders(job_context2)
    check(
        "reminder sent to a user with nothing logged today",
        job_context2.bot.send_message.await_count == 1
        and job_context2.bot.send_message.await_args.kwargs.get("chat_id") == other_user,
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All check-in and reminder checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
