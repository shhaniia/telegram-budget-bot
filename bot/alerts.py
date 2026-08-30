"""Budget threshold alert logic, shared by manual/OCR/recurring/subscription
purchase paths so a crossed threshold is always caught regardless of how the
purchase was logged."""
from telegram.ext import ContextTypes

from bot import db, persona, timeutil


async def check_and_send_threshold_alerts(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int, category_row
) -> None:
    category_id = category_row["id"]
    category_name = category_row["name"]

    for period in ("monthly", "weekly"):
        budget = db.get_budget(user_id, category_id, period)
        if not budget:
            continue
        spent = db.spend_for_category_period(user_id, category_id, period)
        budget_amount = budget["amount"]
        if budget_amount <= 0:
            continue
        pct_spent = (spent / budget_amount) * 100
        pkey = timeutil.period_key(period)

        thresholds = db.thresholds_for_category(user_id, category_id)
        if not thresholds:
            from bot import config

            thresholds = config.DEFAULT_THRESHOLD_PERCENTS

        # Fire the highest crossed threshold that hasn't been sent yet this period.
        crossed = sorted([t for t in thresholds if pct_spent >= t], reverse=True)
        for t in crossed:
            if db.was_alert_sent(user_id, category_id, pkey, t):
                continue
            db.record_alert_sent(user_id, category_id, pkey, t)
            period_word = "week" if period == "weekly" else "month"
            await context.bot.send_message(
                chat_id=chat_id,
                text=persona.threshold_alert(category_name, t, spent, budget_amount, period_word),
                parse_mode="Markdown",
            )
            break  # only the highest newly-crossed threshold, avoid spamming
