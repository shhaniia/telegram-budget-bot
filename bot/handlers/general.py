from telegram import Update
from telegram.ext import ContextTypes

from bot import persona, wizard

HELP_TEXT = (
    "*Logging purchases*\n"
    "`12.50 lunch` — just send an amount + description as a normal message\n"
    "`/log 12.50 Food lunch with the team` — same, explicit\n"
    "Send a *photo* of a receipt or an Apple Pay/Wallet notification screenshot — "
    "I'll OCR it and ask you to confirm before saving\n"
    "Logged the wrong thing? Every entry gets a *Wrong category* / *Discard* button — "
    "or pull up `/recent` and tap 🗑 on any entry, no typing required\n"
    "`/fix <id> <amount>` — correct a misread amount before confirming\n\n"
    "*Categories*\n"
    "`/categories` — list them\n"
    "`/addcategory` — I'll ask what to call it (or `/addcategory Name necessity` to skip straight to it)\n\n"
    "*Budgets & alerts*\n"
    "`/setbudget` — walks you through category, amount, period (or `/setbudget Food 400` directly)\n"
    "`/budgets` — status of all budgets\n"
    "`/setthreshold` — same deal, asks percent and scope (or `/setthreshold 80 Food` directly)\n\n"
    "*Recurring purchases & necessities*\n"
    "`/addrecurring` — guided, or `/addrecurring Rent 1500 Housing monthly necessity` all at once\n"
    "`/recurring` — list them  •  `/removerecurring Rent`\n"
    "These auto-log on their due date and I'll tell you when they do.\n\n"
    "*Subscriptions*\n"
    "`/addsub` — guided, or `/addsub Netflix 15.99 2026-09-05 monthly 3` all at once\n"
    "`/subs` — list active subscriptions + monthly total\n"
    "`/cancelsub Netflix`\n"
    "I'll message you before each renewal with Keep / Cancel / Remind me tomorrow buttons, "
    "and auto-log the charge if you keep it.\n\n"
    "*Reports*\n"
    "`/summary` — this month's breakdown with a little bar chart • also `/summary week` or `/summary year`\n"
    "`/checkin` — on-demand budget pace check: am I over/under/within range for how far into the month it is\n\n"
    "*Reminders & check-ins*\n"
    "`/remind on` — I'll nudge you if a day goes by with nothing logged • `/remind off` to stop\n"
    "I'll also check in on my own, unprompted, mid-month and a few days before month-end, with a budget "
    "pace breakdown — and if a category's been consistently over or under for a few check-ins running, "
    "I'll offer to help you update its budget.\n\n"
    "*Not sure what to do*\n"
    "`/whatsnext` — a short, personalized list of what's actually worth doing based on your recent "
    "history (a missing budget, a repeat purchase worth automating, an off-pace category, ...), each "
    "with a button to act on it right there. Also try just asking \"what should I do next\".\n\n"
    "*Just talk to me*\n"
    "You don't need the exact command — plain English works too, e.g. \"what did I spend today\", "
    "\"set a budget for food\", \"add a subscription\". I'll figure out what you meant and tell you the "
    "command for next time.\n\n"
    "Mid-question and want out? `/cancel` at any point."
)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(persona.welcome(), parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await wizard.cancel_cmd(update, context)
