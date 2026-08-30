import re

from telegram import Update
from telegram.ext import ContextTypes

from bot import db, persona, timeutil, wizard

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AMOUNT_RE = re.compile(r"\d+(?:[.,]\d{1,2})?")
_FREQUENCIES = ("monthly", "yearly", "weekly")


async def add_sub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addsub <name> <amount> <next_billing_date YYYY-MM-DD> [frequency] [reminder_days]
    e.g. /addsub Netflix 15.99 2026-09-05 monthly 3
    With no args at all, I'll ask instead.
    """
    if not context.args:
        await wizard.start(update, context, "addsub")
        return

    text = " ".join(context.args)
    date_match = _DATE_RE.search(text)
    if not date_match:
        await update.message.reply_text(
            "Usage: `/addsub Netflix 15.99 2026-09-05 monthly 3`\n"
            "(date = its next billing/renewal date, YYYY-MM-DD) — or just `/addsub` alone and I'll ask.",
            parse_mode="Markdown",
        )
        return

    try:
        next_billing = timeutil.parse_date(date_match.group())
    except ValueError:
        await update.message.reply_text("That date doesn't look valid — use YYYY-MM-DD.")
        return

    remaining_text = (text[: date_match.start()] + " " + text[date_match.end():]).strip()
    amount_match = _AMOUNT_RE.search(remaining_text)
    if not amount_match:
        await update.message.reply_text(
            "Usage: `/addsub Netflix 15.99 2026-09-05 monthly 3`", parse_mode="Markdown"
        )
        return

    name = remaining_text[: amount_match.start()].strip()
    if not name:
        await update.message.reply_text("Please give the subscription a name, e.g. 'Netflix'.")
        return
    try:
        amount = round(float(amount_match.group().replace(",", ".")), 2)
    except ValueError:
        await update.message.reply_text(persona.bad_amount())
        return

    after_tokens = remaining_text[amount_match.end():].split()
    frequency = "monthly"
    reminder_days = 3
    for tok in after_tokens:
        if tok.lower() in _FREQUENCIES:
            frequency = tok.lower()
        elif tok.isdigit():
            reminder_days = int(tok)

    db.add_subscription(
        user_id=update.effective_user.id,
        name=name,
        amount=amount,
        next_billing_date=next_billing.isoformat(),
        frequency=frequency,
        reminder_days_before=reminder_days,
    )
    await update.message.reply_text(
        persona.sub_added(name, amount, frequency, next_billing.isoformat(), reminder_days),
        parse_mode="Markdown",
    )


async def subs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    subs = db.list_subscriptions(user_id)
    if not subs:
        await update.message.reply_text(persona.sub_empty(), parse_mode="Markdown")
        return
    today = timeutil.today()
    lines = ["*Active subscriptions:*"]
    for s in subs:
        next_date = timeutil.parse_date(s["next_billing_date"])
        days_left = (next_date - today).days
        when = f"in {days_left}d" if days_left >= 0 else f"{-days_left}d overdue"
        lines.append(f"• *{s['name']}* — ${s['amount']:,.2f} {s['frequency']} (next: {s['next_billing_date']}, {when})")
    total = db.monthly_subscription_total(user_id)
    lines.append(f"\n💳 Total: *${total:,.2f}/month* equivalent — hope those are all worth it")
    lines.append("Cancel one with `/cancelsub <name>`.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cancel_sub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/cancelsub Netflix`", parse_mode="Markdown")
        return
    name = " ".join(context.args).strip()
    ok = db.cancel_subscription(update.effective_user.id, name)
    await update.message.reply_text(
        persona.sub_cancelled(name) if ok else f"Couldn't find an active subscription called '{name}'.",
        parse_mode="Markdown",
    )


async def subscription_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, sub_id_str, action = query.data.split(":")
    sub_id = int(sub_id_str)
    sub = db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != update.effective_user.id:
        await query.edit_message_text("Couldn't find that subscription anymore.")
        return

    if action == "cancel":
        db.cancel_subscription_by_id(sub_id)
        await query.edit_message_text(
            f"❌ *{sub['name']}* marked cancelled here. Remember to also cancel it with the actual "
            f"company/App Store — I've only stopped tracking it, I can't reach into their billing system.",
            parse_mode="Markdown",
        )
    elif action == "keep":
        await query.edit_message_text(persona.sub_kept(sub["name"]), parse_mode="Markdown")
    elif action == "snooze":
        db.mark_subscription_reminded(sub_id, "")  # clears so tomorrow's check re-sends it
        await query.edit_message_text(persona.sub_snoozed(sub["name"]), parse_mode="Markdown")
