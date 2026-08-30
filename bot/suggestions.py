"""'What's next?' — lightweight, rules-based personalized suggestions
driven entirely by a user's own recent history (spending patterns, gaps
in budgets/thresholds, off-pace categories, logging habits). No ML, no
external calls — just a priority-ordered set of checks against the same
database every other command reads from.

Powers /whatsnext (and a handful of natural-language phrasings routed to
it via bot/intents.py), and is mentioned in the welcome message so people
know it exists. Each suggestion that has a clear one-tap fix comes with a
button — tapping it jumps straight into the relevant wizard (pre-filled
where we already know the category) or flips the relevant setting, so a
suggestion is something to *act on*, not just read.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import checkins, db, persona, timeutil, wizard

# How many suggestions to show at once — enough to be useful, not enough
# to feel like a chore list.
_MAX_SUGGESTIONS = 3
# A category has to have at least this many purchases this month before
# "turn this into a recurring item" is worth suggesting.
_RECURRING_CANDIDATE_MIN_COUNT = 4
# Ignore near-zero spend in a category when deciding it "needs a budget" —
# not worth a suggestion over a single $1 purchase.
_MIN_SPEND_TO_SUGGEST_BUDGET = 5.0


def _suggestion(text: str, button_label: str | None = None, callback_data: str | None = None) -> dict:
    return {"text": text, "button_label": button_label, "callback_data": callback_data}


def build_suggestions(user_id: int, limit: int = _MAX_SUGGESTIONS) -> list[dict]:
    ever_logged = db.recent_purchases(user_id, limit=1)
    if not ever_logged:
        return [
            _suggestion(
                "You haven't logged anything yet — send an amount like `12.50 lunch` any time, or set a "
                "budget first if you'd rather plan ahead.",
                "🎯 Set a budget",
                "wnaction:setbudget:none",
            )
        ]

    suggestions: list[dict] = []

    # 1. Categories currently on a nudge-worthy over/under-pace streak —
    # same signal the automatic check-in uses, surfaced here too so it's
    # not only ever seen on check-in day.
    for row in db.nudge_worthy_pace_statuses(user_id, checkins._STREAK_TO_NUDGE):
        suggestions.append(
            _suggestion(
                persona.whatsnext_pace_line(row["category_name"], row["status"], row["streak"]),
                f"🔧 Update {row['category_name']} budget",
                f"checkinbudget:{row['category_id']}",
            )
        )

    start, end = timeutil.period_bounds("monthly")
    monthly_spend = db.spend_summary(user_id, start, end)
    budgets = db.list_budgets(user_id)
    budgeted_category_ids = {b["category_id"] for b in budgets}

    # 2. No budgets at all yet, but there's spend to point at.
    if not budgets and monthly_spend:
        top = monthly_spend[0]  # spend_summary is already ordered by total DESC
        suggestions.append(
            _suggestion(
                f"You've spent ${top['total']:,.2f} on *{top['category']}* this month with no budget set "
                "for it — I can't warn you before it gets out of hand.",
                f"🎯 Set a {top['category']} budget",
                f"wnaction:setbudget:{top['category_id']}",
            )
        )
    elif budgets:
        # 3. Top spending category that still has no budget.
        unbudgeted = [r for r in monthly_spend if r["category_id"] not in budgeted_category_ids]
        unbudgeted = [r for r in unbudgeted if r["total"] >= _MIN_SPEND_TO_SUGGEST_BUDGET]
        if unbudgeted:
            top = unbudgeted[0]
            suggestions.append(
                _suggestion(
                    f"*{top['category']}* has ${top['total']:,.2f} logged this month but no budget — "
                    "worth setting one?",
                    f"🎯 Set a {top['category']} budget",
                    f"wnaction:setbudget:{top['category_id']}",
                )
            )

        # 4. Budgets exist but no custom alert threshold anywhere.
        if not db.has_any_threshold(user_id):
            suggestions.append(
                _suggestion(
                    "You've got budgets set but no custom alert threshold — I'll only warn you at the "
                    "defaults (80%/100%) unless you tell me otherwise.",
                    "🔔 Set an alert",
                    "wnaction:setthreshold:none",
                )
            )

    # 5. A category with repeated purchases this month and nothing
    # recurring set up for it — probably a rent/groceries-top-up pattern.
    recurring_category_ids = {r["category_id"] for r in db.list_recurring(user_id)}
    repeat_candidates = [
        r for r in monthly_spend if r["n"] >= _RECURRING_CANDIDATE_MIN_COUNT and r["category_id"] not in recurring_category_ids
    ]
    if repeat_candidates:
        top = repeat_candidates[0]
        suggestions.append(
            _suggestion(
                f"You've logged *{top['category']}* {top['n']} times this month — want me to auto-log it "
                "as a recurring item instead of typing it each time?",
                f"🔁 Automate {top['category']}",
                f"wnaction:addrecurring:{top['category_id']}",
            )
        )

    # 6. Manual purchases sitting in a "Subscriptions"-named category with
    # nothing actually tracked as a subscription.
    sub_category = db.get_category_by_name(user_id, "Subscriptions")
    if sub_category and not db.list_subscriptions(user_id):
        has_manual_sub_spend = any(r["category_id"] == sub_category["id"] for r in monthly_spend)
        if has_manual_sub_spend:
            suggestions.append(
                _suggestion(
                    "You've logged spending under *Subscriptions* but nothing's actually tracked as one — "
                    "`/addsub` gets you a renewal reminder and stops it sneaking past you.",
                    "💳 Track a subscription",
                    "wnaction:addsub:none",
                )
            )

    # 7. Reminder off and yesterday went unlogged, for a user with some history.
    if not db.get_daily_reminder_enabled(user_id):
        has_some_history = len(db.recent_purchases(user_id, limit=3)) >= 3
        yesterday_iso = timeutil.yesterday().isoformat()
        if has_some_history and db.purchase_count_for_date(user_id, yesterday_iso) == 0:
            suggestions.append(
                _suggestion(
                    "Looks like yesterday went unlogged — a daily reminder only pings you on days you "
                    "actually forget.",
                    "⏰ Turn on daily reminder",
                    "wnaction:remind:none",
                )
            )

    # 8. Nothing above applied — positive reinforcement rather than silence.
    if not suggestions and monthly_spend:
        suggestions.append(_suggestion(persona.whatsnext_all_caught_up()))

    return suggestions[:limit]


def _render(suggestions: list[dict]) -> tuple[str, InlineKeyboardMarkup | None]:
    if not suggestions:
        return persona.whatsnext_nothing_yet(), None

    lines = [persona.whatsnext_header(), ""]
    buttons = []
    for s in suggestions:
        lines.append(f"• {s['text']}")
        if s["button_label"] and s["callback_data"]:
            buttons.append([InlineKeyboardButton(s["button_label"], callback_data=s["callback_data"])])
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    return "\n".join(lines), markup


async def whatsnext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/whatsnext — a short, personalized list of what's actually worth
    doing next, based on your own recent history."""
    user_id = update.effective_user.id
    suggestions = build_suggestions(user_id)
    text, markup = _render(suggestions)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def whatsnext_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles a tap on one of /whatsnext's own buttons (the pace-nudge
    button is handled separately by checkins.checkin_budget_callback, since
    it's the same button used there)."""
    query = update.callback_query
    await query.answer()
    _, action, param = query.data.split(":")
    user_id = update.effective_user.id

    await query.edit_message_reply_markup(reply_markup=None)

    if action == "remind":
        db.set_daily_reminder(user_id, True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=persona.reminder_on())
        return

    prefill = {"category_id": int(param)} if param != "none" else None
    await wizard.start(update, context, action, prefill=prefill)
