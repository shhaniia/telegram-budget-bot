"""Daily background job: auto-logs due recurring items/necessities, and
sends subscription renewal reminders + auto-logs subscription charges.

Runs once a day at config.DAILY_CHECK_TIME (bot local timezone). Registered
in main.py via the JobQueue.

Note: reminders and auto-logged entries are sent to chat_id == user_id,
i.e. this assumes each allowed user talks to the bot in a private 1:1 chat
(not a group) — true for the personal-finance use case this bot is built for.
"""
import logging

from telegram.ext import ContextTypes

from bot import db, keyboards, persona, timeutil
from bot.alerts import check_and_send_threshold_alerts

logger = logging.getLogger(__name__)


async def daily_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _process_recurring(context)
    await _process_subscription_reminders(context)
    await _process_subscription_charges(context)


async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Opt-in nudge (see /remind) for users who haven't logged anything yet
    today. Runs once a day at config.REMINDER_TIME — deliberately separate
    from daily_check's morning schedule since this one only makes sense
    once most of the day has actually happened."""
    today_iso = timeutil.today_iso()
    for user_id in db.list_users_with_reminder_enabled():
        try:
            if db.purchase_count_for_date(user_id, today_iso) > 0:
                continue
            await context.bot.send_message(
                chat_id=user_id,
                text=persona.daily_reminder_nudge(),
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Failed sending daily reminder to user_id=%s", user_id)


async def _process_recurring(context: ContextTypes.DEFAULT_TYPE) -> None:
    today_iso = timeutil.today_iso()
    for item in db.due_recurring(today_iso):
        try:
            purchase_id = db.add_purchase(
                user_id=item["user_id"],
                amount=item["amount"],
                category_id=item["category_id"],
                description=f"(recurring) {item['name']}",
                source="recurring",
            )
            next_due = timeutil.add_frequency(timeutil.today(), item["frequency"]).isoformat()
            db.advance_recurring_due(item["id"], next_due)

            await context.bot.send_message(
                chat_id=item["user_id"],
                text=persona.recurring_auto_logged(
                    item["name"], item["amount"], item["category_name"], next_due, bool(item["is_necessity"])
                ),
                parse_mode="Markdown",
                reply_markup=keyboards.log_actions(purchase_id),
            )
            category = db.get_category_by_id(item["user_id"], item["category_id"])
            await check_and_send_threshold_alerts(context, item["user_id"], item["user_id"], category)
        except Exception:
            logger.exception("Failed processing recurring item %s", item["id"])


async def _process_subscription_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = timeutil.today()
    for sub in db.list_all_active_subscriptions():
        try:
            next_billing = timeutil.parse_date(sub["next_billing_date"])
            days_until = (next_billing - today).days
            already_reminded = sub["last_reminded_for"] == sub["next_billing_date"]
            if 0 <= days_until <= sub["reminder_days_before"] and not already_reminded:
                db.mark_subscription_reminded(sub["id"], sub["next_billing_date"])
                await context.bot.send_message(
                    chat_id=sub["user_id"],
                    text=persona.sub_reminder(sub["name"], days_until, sub["next_billing_date"], sub["amount"]),
                    parse_mode="Markdown",
                    reply_markup=keyboards.subscription_reminder(sub["id"]),
                )
        except Exception:
            logger.exception("Failed processing subscription reminder %s", sub["id"])


async def _process_subscription_charges(context: ContextTypes.DEFAULT_TYPE) -> None:
    today_iso = timeutil.today_iso()

    for sub in db.due_subscription_charges(today_iso):
        try:
            category = db.get_category_by_name(sub["user_id"], "Subscriptions") or db.get_category_by_name(
                sub["user_id"], "Other"
            )
            purchase_id = db.add_purchase(
                user_id=sub["user_id"],
                amount=sub["amount"],
                category_id=category["id"],
                description=f"(subscription) {sub['name']}",
                source="subscription",
            )
            next_billing = timeutil.add_frequency(
                timeutil.parse_date(sub["next_billing_date"]), sub["frequency"]
            ).isoformat()
            db.advance_subscription_billing(sub["id"], next_billing)
            db.mark_subscription_reminded(sub["id"], "")  # reset for the new cycle

            await context.bot.send_message(
                chat_id=sub["user_id"],
                text=persona.sub_charged(sub["name"], sub["amount"], category["name"], next_billing),
                parse_mode="Markdown",
                reply_markup=keyboards.log_actions(purchase_id),
            )
            await check_and_send_threshold_alerts(context, sub["user_id"], sub["user_id"], category)
        except Exception:
            logger.exception("Failed processing subscription charge %s", sub["id"])
