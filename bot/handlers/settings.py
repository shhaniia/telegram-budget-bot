"""Per-user notification preferences. Right now that's just the opt-in
daily "did you log anything today?" reminder — see bot/jobs.py
(send_daily_reminders) and bot/persona.py for the message itself."""
from telegram import Update
from telegram.ext import ContextTypes

from bot import db, persona


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/remind — shows the current setting. /remind on|off — changes it."""
    user_id = update.effective_user.id
    if not context.args:
        enabled = db.get_daily_reminder_enabled(user_id)
        await update.message.reply_text(persona.reminder_status(enabled), parse_mode="Markdown")
        return

    choice = context.args[0].lower()
    if choice in ("on", "yes", "enable", "enabled"):
        db.set_daily_reminder(user_id, True)
        await update.message.reply_text(persona.reminder_on())
    elif choice in ("off", "no", "disable", "disabled"):
        db.set_daily_reminder(user_id, False)
        await update.message.reply_text(persona.reminder_off())
    else:
        await update.message.reply_text("Usage: `/remind on` or `/remind off`.", parse_mode="Markdown")
