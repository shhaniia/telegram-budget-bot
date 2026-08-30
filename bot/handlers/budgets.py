from telegram import Update
from telegram.ext import ContextTypes

from bot import db, persona, wizard


def _progress_bar(pct: float, width: int = 10) -> str:
    filled = min(width, int(round(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


async def set_budget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setbudget <category> <amount> [monthly|weekly] — or with no args, I'll ask."""
    args = list(context.args)
    if not args:
        await wizard.start(update, context, "setbudget")
        return

    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/setbudget Food 400` or `/setbudget Food 100 weekly` — "
            "or just `/setbudget` alone and I'll walk you through it.",
            parse_mode="Markdown",
        )
        return

    period = "monthly"
    if args[-1].lower() in ("weekly", "monthly"):
        period = args[-1].lower()
        args = args[:-1]

    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/setbudget Food 400` or `/setbudget Food 100 weekly`",
            parse_mode="Markdown",
        )
        return

    try:
        amount = round(float(args[-1].replace(",", ".")), 2)
    except ValueError:
        await update.message.reply_text(persona.bad_amount())
        return
    if amount <= 0:
        await update.message.reply_text("Budget amount needs to be greater than zero.")
        return

    category_name = " ".join(args[:-1]).strip()
    category = db.get_category_by_name(update.effective_user.id, category_name)
    if not category:
        await update.message.reply_text(
            f"No category called '{category_name}'. See `/categories`, or add it with "
            f"`/addcategory {category_name}` first.",
            parse_mode="Markdown",
        )
        return

    db.set_budget(update.effective_user.id, category["id"], amount, period)
    await update.message.reply_text(persona.budget_set(category["name"], amount, period), parse_mode="Markdown")


async def budgets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    budgets = db.list_budgets(user_id)
    if not budgets:
        await update.message.reply_text(persona.no_budgets_yet(), parse_mode="Markdown")
        return

    lines = ["*Budget status:*\n"]
    for b in budgets:
        category = db.get_category_by_id(user_id, b["category_id"])
        spent = db.spend_for_category_period(user_id, b["category_id"], b["period"])
        pct = (spent / b["amount"] * 100) if b["amount"] else 0
        bar = _progress_bar(pct)
        flag = " 🚨" if pct >= 100 else (" ⚠️" if pct >= 80 else "")
        period_word = "wk" if b["period"] == "weekly" else "mo"
        lines.append(
            f"*{category['name']}* ({period_word}){flag}\n"
            f"{bar}  ${spent:,.2f} / ${b['amount']:,.2f} ({pct:.0f}%)"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def set_threshold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setthreshold 80 [Category] — or with no args, I'll ask."""
    args = list(context.args)
    if not args:
        await wizard.start(update, context, "setthreshold")
        return

    try:
        percent = int(args[0])
    except ValueError:
        await update.message.reply_text("First argument must be a whole-number percent, e.g. 80.")
        return
    if not (1 <= percent <= 300):
        await update.message.reply_text("Percent should be between 1 and 300.")
        return

    category_id = None
    scope_label = "all categories"
    if len(args) > 1:
        category_name = " ".join(args[1:]).strip()
        category = db.get_category_by_name(update.effective_user.id, category_name)
        if not category:
            await update.message.reply_text(f"No category called '{category_name}'.")
            return
        category_id = category["id"]
        scope_label = f"'{category['name']}'"

    db.set_threshold(update.effective_user.id, percent, category_id)
    await update.message.reply_text(persona.threshold_set(percent, scope_label), parse_mode="Markdown")
