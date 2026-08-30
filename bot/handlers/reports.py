from telegram import Update
from telegram.ext import ContextTypes

from bot import db, timeutil

_PERIOD_ALIASES = {
    "today": "today",
    "day": "today",
    "week": "week",
    "wk": "week",
    "month": "month",
    "mo": "month",
    "year": "year",
    "yr": "year",
}

_BAR_WIDTH = 14
_NAME_WIDTH = 13


def _bounds_for(period: str):
    today = timeutil.today()
    if period == "today":
        return today, today
    if period == "week":
        return timeutil.period_bounds("weekly")
    if period == "year":
        return today.replace(month=1, day=1), today.replace(month=12, day=31)
    return timeutil.period_bounds("monthly")  # default: month


def _text_bar_chart(rows) -> str:
    """Horizontal bar chart, one row per category, scaled so the biggest
    spender gets a full-width bar. Rendered inside a code block so the
    columns actually line up in Telegram's monospace font."""
    if not rows:
        return ""
    max_total = max(r["total"] for r in rows) or 1
    lines = []
    for r in rows:
        filled = max(1, round(r["total"] / max_total * _BAR_WIDTH))
        bar = "█" * filled + "░" * (_BAR_WIDTH - filled)
        name = r["category"][:_NAME_WIDTH].ljust(_NAME_WIDTH)
        lines.append(f"{name} {bar} ${r['total']:,.2f}")
    return "```\n" + "\n".join(lines) + "\n```"


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/summary [today|week|month|year] — defaults to month (MTD)."""
    arg = (context.args[0].lower() if context.args else "month")
    period = _PERIOD_ALIASES.get(arg, "month")
    start, end = _bounds_for(period)

    user_id = update.effective_user.id
    rows = db.spend_summary(user_id, start, end)
    if not rows:
        await update.message.reply_text(
            f"Nothing logged this {period} — either you're broke-proof or you forgot to tell me about something."
        )
        return

    total = sum(r["total"] for r in rows)
    necessity_total = sum(r["total"] for r in rows if r["is_necessity"])
    discretionary_total = total - necessity_total

    period_label = {"today": "Today", "week": "This week", "year": "This year"}.get(period, "This month")
    header = f"📊 *{period_label}'s breakdown* ({start.isoformat()} → {end.isoformat()})"

    lines = [header, "", _text_bar_chart(rows), ""]
    for r in rows:
        pct = (r["total"] / total * 100) if total else 0
        tag = " 🔒" if r["is_necessity"] else ""
        lines.append(f"• {r['category']}{tag}: ${r['total']:,.2f} ({pct:.0f}%, {r['n']} purchase{'s' if r['n'] != 1 else ''})")

    lines.append(f"\n*Total: ${total:,.2f}*")
    lines.append(f"🔒 Necessities: ${necessity_total:,.2f}  •  Discretionary: ${discretionary_total:,.2f}")

    sub_total = db.monthly_subscription_total(user_id)
    if sub_total and period == "month":
        lines.append(f"💳 Subscriptions add another ~${sub_total:,.2f}/month on top of that, by the way.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
