import re

from telegram import Update
from telegram.ext import ContextTypes

from bot import db, persona, timeutil, wizard

_AMOUNT_RE = re.compile(r"\d+(?:[.,]\d{1,2})?")
_FREQUENCIES = ("daily", "weekly", "monthly", "yearly")


async def add_recurring_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addrecurring <name> <amount> <category> [frequency] [necessity]
    e.g. /addrecurring Rent 1500 Housing monthly necessity
         /addrecurring Groceries top-up 60 Groceries weekly necessity
    With no args at all, I'll ask instead of making you remember the syntax.
    """
    if not context.args:
        await wizard.start(update, context, "addrecurring")
        return

    text = " ".join(context.args)
    match = _AMOUNT_RE.search(text)
    if not match:
        await update.message.reply_text(
            "Usage: `/addrecurring Rent 1500 Housing monthly necessity` "
            "(or just `/addrecurring` alone and I'll walk you through it).",
            parse_mode="Markdown",
        )
        return

    name = text[: match.start()].strip()
    amount_str = match.group().replace(",", ".")
    remainder_tokens = text[match.end():].split()

    if not name or not remainder_tokens:
        await update.message.reply_text(
            "Usage: `/addrecurring Rent 1500 Housing monthly necessity`",
            parse_mode="Markdown",
        )
        return

    is_necessity = False
    if remainder_tokens and remainder_tokens[-1].lower() in ("necessity", "necessary"):
        is_necessity = True
        remainder_tokens = remainder_tokens[:-1]

    frequency = "monthly"
    if remainder_tokens and remainder_tokens[-1].lower() in _FREQUENCIES:
        frequency = remainder_tokens[-1].lower()
        remainder_tokens = remainder_tokens[:-1]

    category_name = " ".join(remainder_tokens).strip()
    if not category_name:
        await update.message.reply_text("Please include a category, e.g. `... 1500 Housing monthly`.", parse_mode="Markdown")
        return

    category = db.get_category_by_name(update.effective_user.id, category_name)
    if not category:
        await update.message.reply_text(f"No category called '{category_name}'. See `/categories`.", parse_mode="Markdown")
        return

    try:
        amount = round(float(amount_str), 2)
    except ValueError:
        await update.message.reply_text(persona.bad_amount())
        return

    next_due = timeutil.add_frequency(timeutil.today(), frequency).isoformat()
    db.add_recurring(
        user_id=update.effective_user.id,
        name=name,
        amount=amount,
        category_id=category["id"],
        frequency=frequency,
        is_necessity=is_necessity,
        next_due=next_due,
    )
    await update.message.reply_text(
        persona.recurring_added(name, amount, frequency, category["name"], next_due),
        parse_mode="Markdown",
    )


async def recurring_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_recurring(update.effective_user.id)
    if not rows:
        await update.message.reply_text(persona.recurring_empty(), parse_mode="Markdown")
        return
    lines = ["*Recurring purchases:*"]
    for r in rows:
        tag = " 🔒" if r["is_necessity"] else ""
        lines.append(
            f"• *{r['name']}*{tag} — ${r['amount']:,.2f} {r['frequency']} "
            f"[{r['category_name']}], next: {r['next_due']}"
        )
    lines.append("\n🔒 = necessity. Done with one? `/removerecurring <name>`.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def remove_recurring_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/removerecurring Rent`", parse_mode="Markdown")
        return
    name = " ".join(context.args).strip()
    ok = db.deactivate_recurring(update.effective_user.id, name)
    await update.message.reply_text(
        f"Removed '{name}'. One less thing to auto-log." if ok else f"Couldn't find a recurring item called '{name}'."
    )
