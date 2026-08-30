"""Automatic mid-month and near-month-end budget check-ins.

Compares, per category with a monthly budget, how much of the budget has
been used against how much of the month has elapsed — a "pace" ratio —
and buckets each category as running over/under/within that pace. Also
tracks a streak per category so that a budget that's been consistently
off-pace for a few check-ins running gets flagged with a one-tap prompt
to update it, instead of silently nagging forever.

Runs once a day (wired into the job queue in main.py) but only actually
sends anything on the two days of the month it cares about: day 15
("mid-month") and 3 days before the last day of the month ("eom3"). Also
exposed as /checkin for an on-demand, read-only look any day.
"""
from __future__ import annotations

import calendar
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import db, persona, timeutil, wizard

logger = logging.getLogger(__name__)

# How far the used-budget% can drift from the elapsed-month% before we call
# it "over" or "under" pace rather than "within range".
_OVER_RATIO = 1.15
_UNDER_RATIO = 0.85
# "More than 3 check-ins in a row" off-pace before we prompt to update the
# budget, per the spec — i.e. a streak has to exceed 3.
_STREAK_TO_NUDGE = 4


def checkin_type_for(today) -> str | None:
    """Which automatic check-in (if any) today is: day 15 ('midmonth'), or
    3 days before the last day of the month ('eom3'). None on every other day."""
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day == 15:
        return "midmonth"
    if today.day == days_in_month - 3:
        return "eom3"
    return None


def _classify(pct_used: float, pct_elapsed: float) -> str:
    if pct_elapsed <= 0:
        return "within"
    ratio = pct_used / pct_elapsed
    if ratio >= _OVER_RATIO:
        return "over"
    if ratio <= _UNDER_RATIO:
        return "under"
    return "within"


def build_report(user_id: int, today, checkin_type: str | None = None, persist: bool = False):
    """Builds the pace-check message for a user.

    When `persist` is True (the automatic scheduled run), each category's
    over/under/within streak is advanced and saved. When False (the
    on-demand /checkin command), it's read-only: streaks are reported as
    they currently stand but never advanced, so checking in manually can't
    itself trigger — or delay — the "update your budget?" nudge.

    Returns (message_text, nudge_worthy_categories). message_text is ""
    when the user has no monthly budgets to report on.
    """
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    pct_elapsed = today.day / days_in_month * 100

    budgets = [b for b in db.list_budgets(user_id) if b["period"] == "monthly"]
    if not budgets:
        return "", []

    checkin_key = f"{today.year:04d}-{today.month:02d}-{checkin_type}" if checkin_type else None
    header = persona.checkin_header(checkin_type) if checkin_type else persona.checkin_header_ondemand()
    lines = [header, ""]
    nudge_worthy = []

    for b in budgets:
        category_id = b["category_id"]
        spent = db.spend_for_category_period(user_id, category_id, "monthly")
        pct_used = (spent / b["amount"] * 100) if b["amount"] else 0
        status = _classify(pct_used, pct_elapsed)
        existing = db.get_pace_status(user_id, category_id)

        if persist:
            if existing and existing["last_checkin_key"] == checkin_key:
                streak = existing["streak"]  # already processed for this check-in, don't double-count
            else:
                streak = (existing["streak"] + 1) if (existing and existing["status"] == status) else 1
                db.upsert_pace_status(user_id, category_id, status, streak, checkin_key)
        else:
            streak = existing["streak"] if (existing and existing["status"] == status) else 0

        lines.append(persona.pace_line(b["category_name"], pct_used, pct_elapsed, status))
        if status != "within" and streak >= _STREAK_TO_NUDGE:
            nudge_worthy.append(
                {
                    "category_id": category_id,
                    "category_name": b["category_name"],
                    "status": status,
                    "streak": streak,
                }
            )

    lines.append(f"\n_{pct_elapsed:.0f}% of the month gone — that's what pace is measured against._")
    return "\n".join(lines), nudge_worthy


async def _send_nudges(context: ContextTypes.DEFAULT_TYPE, chat_id: int, nudge_worthy: list) -> None:
    for item in nudge_worthy:
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"🔧 Update {item['category_name']} budget",
                        callback_data=f"checkinbudget:{item['category_id']}",
                    )
                ]
            ]
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=persona.budget_nudge(item["category_name"], item["status"], item["streak"]),
            parse_mode="Markdown",
            reply_markup=markup,
        )


async def run_periodic_checkins(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue entry point — no-ops on every day that isn't a check-in day."""
    today = timeutil.today()
    checkin_type = checkin_type_for(today)
    if checkin_type is None:
        return
    for user_id in db.list_users_with_monthly_budgets():
        try:
            text, nudge_worthy = build_report(user_id, today, checkin_type=checkin_type, persist=True)
            if not text:
                continue
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
            await _send_nudges(context, user_id, nudge_worthy)
        except Exception:
            logger.exception("Failed sending check-in to user_id=%s", user_id)


async def checkin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/checkin — see the pace report on demand, any day of the month."""
    user_id = update.effective_user.id
    today = timeutil.today()
    text, nudge_worthy = build_report(user_id, today, checkin_type=None, persist=False)
    if not text:
        await update.message.reply_text(
            "No monthly budgets set yet, so there's no pace to check. `/setbudget` first."
        )
        return
    await update.message.reply_text(text, parse_mode="Markdown")
    await _send_nudges(context, update.effective_chat.id, nudge_worthy)


async def checkin_budget_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the 'Update X budget' button attached to a nudge — jumps
    straight into the setbudget wizard with the category pre-filled."""
    query = update.callback_query
    await query.answer()
    _, category_id_str = query.data.split(":")
    category_id = int(category_id_str)
    user_id = update.effective_user.id
    category = db.get_category_by_id(user_id, category_id)
    if not category:
        await query.edit_message_text("Couldn't find that category anymore.")
        return
    await query.edit_message_reply_markup(reply_markup=None)
    await wizard.start(update, context, "setbudget", prefill={"category_id": category_id})
