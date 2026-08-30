"""Entry point. Run with: python main.py (after filling in .env)."""
import datetime
import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from bot import checkins, config, db, jobs, suggestions, timeutil, wizard
from bot.handlers import budgets, categories, general, purchases, recurring, reports, settings, subscriptions

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("budget-bot")


async def _guard_allowed_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every other handler (group=-1). In private mode, blocks
    anyone not in ALLOWED_USER_IDS. In public mode (empty allow-list),
    everyone gets through, but this is also where we make sure a
    first-time user has their own set of default categories before any
    other handler runs."""
    user = update.effective_user
    if user is None:
        return
    if not config.PUBLIC_MODE and user.id not in config.ALLOWED_USER_IDS:
        logger.warning("Blocked message from unauthorized user_id=%s", user.id)
        if update.effective_message:
            await update.effective_message.reply_text(
                "This bot is private and isn't available to you."
            )
        elif update.callback_query:
            await update.callback_query.answer("Not authorized.", show_alert=True)
        raise ApplicationHandlerStop
    db.ensure_categories_seeded(user.id)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing update: %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Something went wrong handling that — I've logged it. Try again, or /help."
            )
        except Exception:
            pass


def build_application() -> Application:
    application = Application.builder().token(config.BOT_TOKEN).build()

    # Allow-list guard first, ahead of every other handler.
    application.add_handler(TypeHandler(Update, _guard_allowed_users), group=-1)

    application.add_handler(CommandHandler("start", general.start_cmd))
    application.add_handler(CommandHandler("help", general.help_cmd))
    application.add_handler(CommandHandler("cancel", general.cancel_cmd))

    application.add_handler(CommandHandler("log", purchases.log_cmd))
    application.add_handler(CommandHandler("recent", purchases.recent_cmd))
    application.add_handler(CommandHandler("undo", purchases.undo_cmd))
    application.add_handler(CommandHandler("fix", purchases.fix_amount_cmd))

    application.add_handler(CommandHandler("categories", categories.categories_cmd))
    application.add_handler(CommandHandler("addcategory", categories.add_category_cmd))

    application.add_handler(CommandHandler("setbudget", budgets.set_budget_cmd))
    application.add_handler(CommandHandler(["budgets", "budget"], budgets.budgets_cmd))
    application.add_handler(CommandHandler("setthreshold", budgets.set_threshold_cmd))

    application.add_handler(CommandHandler("addrecurring", recurring.add_recurring_cmd))
    application.add_handler(CommandHandler("recurring", recurring.recurring_cmd))
    application.add_handler(CommandHandler("removerecurring", recurring.remove_recurring_cmd))

    application.add_handler(CommandHandler("addsub", subscriptions.add_sub_cmd))
    application.add_handler(CommandHandler("subs", subscriptions.subs_cmd))
    application.add_handler(CommandHandler("cancelsub", subscriptions.cancel_sub_cmd))

    application.add_handler(CommandHandler("summary", reports.summary_cmd))
    application.add_handler(CommandHandler("checkin", checkins.checkin_cmd))
    application.add_handler(CommandHandler("whatsnext", suggestions.whatsnext_cmd))

    application.add_handler(CommandHandler("remind", settings.remind_cmd))

    # Callback query (inline button) handlers, routed by data prefix.
    application.add_handler(CallbackQueryHandler(purchases.category_picker_callback, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(purchases.confirm_callback, pattern=r"^confirm:"))
    application.add_handler(CallbackQueryHandler(purchases.recategorize_open_callback, pattern=r"^recatopen:"))
    application.add_handler(CallbackQueryHandler(purchases.recategorize_callback, pattern=r"^recat:"))
    application.add_handler(CallbackQueryHandler(purchases.undo_callback, pattern=r"^undo:"))
    application.add_handler(CallbackQueryHandler(purchases.recent_undo_callback, pattern=r"^recentundo:"))
    application.add_handler(
        CallbackQueryHandler(subscriptions.subscription_reminder_callback, pattern=r"^sub:")
    )
    application.add_handler(CallbackQueryHandler(wizard.handle_callback, pattern=r"^wiz:"))
    application.add_handler(
        CallbackQueryHandler(checkins.checkin_budget_callback, pattern=r"^checkinbudget:")
    )
    application.add_handler(
        CallbackQueryHandler(suggestions.whatsnext_action_callback, pattern=r"^wnaction:")
    )

    # Plain messages: photo -> OCR flow, any other text -> quick-log parser.
    application.add_handler(MessageHandler(filters.PHOTO, purchases.photo_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, purchases.text_message))

    application.add_error_handler(_on_error)

    hour, minute = (int(p) for p in config.DAILY_CHECK_TIME.split(":"))
    application.job_queue.run_daily(
        jobs.daily_check,
        time=datetime.time(hour=hour, minute=minute, tzinfo=timeutil.TZ),
        name="daily_check",
    )
    application.job_queue.run_daily(
        checkins.run_periodic_checkins,
        time=datetime.time(hour=hour, minute=minute, tzinfo=timeutil.TZ),
        name="periodic_checkins",
    )

    reminder_hour, reminder_minute = (int(p) for p in config.REMINDER_TIME.split(":"))
    application.job_queue.run_daily(
        jobs.send_daily_reminders,
        time=datetime.time(hour=reminder_hour, minute=reminder_minute, tzinfo=timeutil.TZ),
        name="daily_reminders",
    )

    return application


def main() -> None:
    problems = config.validate()
    if problems:
        for p in problems:
            logger.error("Config problem: %s", p)
        sys.exit(1)

    db.init_db()
    logger.info("Database ready at %s", config.DATABASE_PATH)
    if config.PUBLIC_MODE:
        logger.warning("Running in PUBLIC mode — anyone on Telegram can use this bot.")
    else:
        logger.info("Allowed user IDs: %s", config.ALLOWED_USER_IDS)

    application = build_application()
    logger.info("Bot starting (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
