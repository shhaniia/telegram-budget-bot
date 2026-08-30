"""The bot's voice: chirpy, a little sarcastic, never actually mean —
picture Rick Sanchez if he liked you and wanted your budget to work out.
Every user-facing string in the handlers should route through here rather
than being hand-written inline, so the tone stays consistent (and varied,
since most pickers rotate between a few lines instead of repeating one).

Keep it to one quip per message, garnish not entree — the numbers always
come through clearly, the wit sits around them.
"""
from __future__ import annotations

import random


def pick(*variants: str) -> str:
    return random.choice(variants)


# ------------------------------------------------------------------ general

def welcome() -> str:
    return (
        "Oh good, you're here. 🧪 I'm your budget bot — think of me as the "
        "friend who actually remembers where your money went, minus the "
        "judgment (mostly).\n\n"
        "*Here's the tour:*\n"
        "💸 *Log spending* — send `12.50 lunch` like a normal message, or a photo of a receipt "
        "or Apple Pay/Wallet notification\n"
        "📊 *See where it went* — `/recent`, `/summary`, `/budgets`\n"
        "🎯 *Set limits* — `/setbudget`, `/setthreshold` for alerts\n"
        "🔁 *Automate it* — `/addrecurring` for rent/bills, `/addsub` for subscriptions "
        "(I'll warn you before they renew)\n"
        "⏰ *Nudges* — `/remind on` for a daily \"did you log anything today\" ping, and I'll "
        "check in on your own with a budget pace report mid-month and near month-end\n"
        "🧭 *Not sure what to do next?* Send `/whatsnext` any time — I'll look at your actual "
        "recent spending and tell you what's worth doing (a missing budget, a repeat purchase "
        "worth automating, whatever's relevant), not a generic checklist. Each suggestion comes "
        "with a button so you can just tap it.\n\n"
        "You don't need to memorize any of that, though — just tell me what you want in plain "
        "English (\"what did I spend today\", \"set a budget for food\") and I'll figure out "
        "which command you meant.\n\n"
        "Send /help any time for the full list. Which, statistically, you'll need eventually."
    )


def help_footer() -> str:
    return (
        "\nMost of these also work with zero arguments — just send the bare "
        "command and I'll ask you questions instead of making you remember "
        "syntax. Revolutionary, I know."
    )


# ---------------------------------------------------------------- purchases

def logged_line(amount: float, category: str) -> str:
    template = pick(
        "💸 Logged! ${amount:,.2f} evaporates into *{category}*.",
        "✅ Got it — ${amount:,.2f} filed under *{category}*.",
        "📝 Noted. ${amount:,.2f} added to *{category}*. History remembers.",
        "💰 ${amount:,.2f} → *{category}*. Done, dusted, logged.",
    )
    return template.format(amount=amount, category=category)


def pending_amount_only(amount: float, description: str) -> str:
    base = pick(
        "Got ${amount:,.2f}{desc}. Which category does this belong to?",
        "${amount:,.2f}{desc} — cool, but I need a category to file it under.",
        "${amount:,.2f}{desc}. Where does this go? Pick one below.",
    )
    desc = f" — _{description}_" if description else ""
    return base.format(amount=amount, desc=desc)


def no_amount_found() -> str:
    return pick(
        "I couldn't spot an amount in there. Try something like `12.50 lunch`.",
        "No number jumped out at me. Give me an amount, like `12.50 lunch`.",
        "Hmm, no amount detected. `/help` has examples if you need a nudge.",
    )


def bad_amount() -> str:
    return pick(
        "That doesn't look like a number I trust. Try again?",
        "I need an actual amount there — digits, maybe a decimal point.",
    )


def discarded() -> str:
    return pick(
        "🗑 Poof. Discarded, like it never happened.",
        "🗑 Gone. Wiped from the record, no questions asked.",
        "🗑 Deleted. My memory is now conveniently blank on that one.",
    )


def undone() -> str:
    return pick(
        "↩ Undone — that entry's been erased.",
        "↩ Reversed! Consider it un-spent, at least in here.",
        "↩ Yanked out of the ledger. Never happened.",
    )


def already_gone() -> str:
    return "That one's already gone — someone beat you to it (probably you)."


def recent_empty(label: str) -> str:
    return f"Nothing logged {label.lower()}. A blank slate — dangerous."


def recent_footer() -> str:
    return pick(
        "Tap 🗑 on one below to discard it instantly — no need to remember its number.",
        "Wrong entry in there? Tap its 🗑 button below — gone, no typing required.",
    )


def delete_confirmed(amount: float, category: str, description: str) -> str:
    desc = f" — {description}" if description else ""
    return pick(
        f"↩ Gone: ${amount:,.2f} · {category}{desc}. Like it never happened.",
        f"↩ Removed ${amount:,.2f} from {category}{desc}. Ledger's lighter already.",
    )


def delete_not_found() -> str:
    return pick(
        "Couldn't find a purchase matching that. `/recent` will show you the ids if you want to be precise.",
        "Nothing matched that description — try `/recent` and tap the 🗑, or double-check the amount.",
    )


def delete_ambiguous() -> str:
    return "Found more than one that could match — tap the right one:"


def delete_needs_more_detail() -> str:
    return (
        "Which purchase? Give me an amount or a word from the description — "
        "\"remove the $5 lunch\" — or check `/recent` and tap 🗑 there."
    )


def entry_expired() -> str:
    return pick(
        "This one expired while we were chatting. Just send it again.",
        "That entry's gone stale — resend it and we'll try again.",
    )


def ocr_reading() -> str:
    return pick(
        "🔎 Squinting at your photo…",
        "🔎 Reading the fine print, one sec…",
        "🔎 OCR-ing this thing, hold tight…",
    )


def ocr_failed() -> str:
    return (
        "Yeah, I got nothing from that image — OCR tapped out. Log it "
        "manually instead, like `12.50 groceries`, and we'll pretend this "
        "didn't happen."
    )


def ocr_result(amount: float, description: str, category: str | None) -> str:
    lines = [pick("📷 Read ${amount:,.2f} off that.", "📷 I make it ${amount:,.2f}.").format(amount=amount)]
    if description:
        lines.append(f"_{description}_")
    if category:
        lines.append(f"Best guess on category: *{category}*. Right?")
    else:
        lines.append("No idea what category that is though — help me out.")
    return "\n".join(lines)


def ocr_no_amount(pending_id: int) -> str:
    return (
        "I stared at that image and came up empty on the amount — my OCR "
        f"has limits. Fix it with `/fix {pending_id} 12.50`, or just log it "
        "manually."
    )


def recategorized(category: str) -> str:
    return pick(
        f"✅ Moved to *{category}*. Filing error corrected.",
        f"✅ Reassigned to *{category}*. My bad, or yours, doesn't matter now.",
    ).replace("$CAT", category)


def wrong_category_prompt() -> str:
    return pick("Okay, where *should* it go?", "Fine, pick the real one:")


# ------------------------------------------------------------------- budget

def budget_set(category: str, amount: float, period: str) -> str:
    return pick(
        f"✅ {period.capitalize()} budget for *{category}*: ${amount:,.2f}. Try to stay under it, I believe in you.",
        f"✅ *{category}* now capped at ${amount:,.2f} per {period[:-2] if period.endswith('ly') else period}. Good luck out there.",
    )


def no_budgets_yet() -> str:
    return "No budgets set yet — you're spending in the dark. Send `/setbudget` and let's fix that."


def threshold_set(percent: int, scope: str) -> str:
    return f"✅ I'll yell at {percent}% for {scope}. Consider yourself warned in advance."


def threshold_alert(category: str, percent: int, spent: float, budget: float, period_word: str) -> str:
    if percent >= 100:
        opener = pick(
            f"🚨 *{category}* just blew past its {period_word}ly budget.",
            f"🚨 Well, *{category}* is officially over budget for the {period_word}.",
            f"🚨 That's a wrap on *{category}*'s {period_word}ly budget — and not in a good way.",
        )
    else:
        opener = pick(
            f"⚠️ *{category}* just hit {percent}% of its {period_word}ly budget.",
            f"⚠️ Heads up — *{category}* is at {percent}% for the {period_word}.",
        )
    return f"{opener}\nSpent ${spent:,.2f} of ${budget:,.2f}."


# ---------------------------------------------------------------- categories

def category_exists(name: str) -> str:
    return f"'{name}' already exists — I checked twice, I'm thorough like that."


def category_added(name: str, is_necessity: bool) -> str:
    tag = " Filed as a necessity — very responsible of you." if is_necessity else " Filed as a treat — no judgment."
    return f"✅ New category: *{name}*.{tag}"


def categories_empty() -> str:
    return "You have zero categories, which is a bold minimalist choice. Let's add one with `/addcategory`."


# ---------------------------------------------------------------- recurring

def recurring_added(name: str, amount: float, frequency: str, category: str, next_due: str) -> str:
    return (
        f"✅ *{name}* — ${amount:,.2f} {frequency}, filed under {category}.\n"
        f"I'll quietly log it on {next_due} and ping you when I do."
    )


def recurring_empty() -> str:
    return "No recurring items on file. Rent doesn't track itself — `/addrecurring` to fix that."


def recurring_auto_logged(name: str, amount: float, category: str, next_due: str, necessity: bool) -> str:
    tag = " 🔒" if necessity else ""
    return (
        f"🔁 Auto-logged: *{name}*{tag}\n"
        f"${amount:,.2f} — {category}\n"
        f"Next one: {next_due}"
    )


# ------------------------------------------------------------- subscriptions

def sub_added(name: str, amount: float, frequency: str, next_date: str, reminder_days: int) -> str:
    return (
        f"✅ Tracking *{name}* — ${amount:,.2f} {frequency}, next charge {next_date}.\n"
        f"I'll nag you {reminder_days} day(s) before it renews, in case you forgot you had it."
    )


def sub_empty() -> str:
    return "No subscriptions tracked yet. Somewhere, a free trial is quietly becoming a real charge. `/addsub` to catch it."


def sub_cancelled(name: str) -> str:
    return pick(
        f"❌ '{name}' cancelled here. Go cancel it with the actual company too — I only control what happens in this chat.",
        f"❌ Marked '{name}' as cancelled. Now go tell the real subscription — it doesn't read Telegram.",
    )


def sub_reminder(name: str, days_until: int, date: str, amount: float) -> str:
    when = "today" if days_until == 0 else f"in {days_until} day(s) ({date})"
    return (
        f"🔔 *{name}* renews {when} for ${amount:,.2f}.\n"
        f"Keeping it, or finally cancelling that thing?"
    )


def sub_kept(name: str) -> str:
    return f"✅ Keeping *{name}*. No shame — I'll remind you again next time."


def sub_snoozed(name: str) -> str:
    return f"⏰ Fine, I'll bug you about *{name}* again tomorrow."


def sub_charged(name: str, amount: float, category: str, next_date: str) -> str:
    return (
        f"💳 *{name}* charged ${amount:,.2f}, logged under {category}.\n"
        f"Next renewal: {next_date}."
    )


# -------------------------------------------------------------------- setup

def wizard_cancelled() -> str:
    return pick(
        "🛑 Scrapped it. No harm done.",
        "🛑 Cancelled — as if we never spoke.",
        "🛑 Aborted. Onward.",
    )

def wizard_no_active() -> str:
    return "Nothing to cancel — you weren't in the middle of anything."


# ----------------------------------------------------------------- reminders

def reminder_on() -> str:
    return pick(
        "⏰ Done — I'll nudge you if a day goes by with nothing logged. `/remind off` whenever you've had enough.",
        "⏰ Reminders on. I'll only bug you on days you haven't logged anything — `/remind off` to stop.",
    )


def reminder_off() -> str:
    return pick(
        "🔕 Reminders off. I'll stay quiet — `/remind on` is right there if you change your mind.",
        "🔕 Turned it off. No more nagging from me. About this, anyway.",
    )


def reminder_status(enabled: bool) -> str:
    if enabled:
        return "⏰ Daily reminders are *on*. Turn them off with `/remind off`."
    return "🔕 Daily reminders are *off*. Turn them on with `/remind on`."


def daily_reminder_nudge() -> str:
    body = pick(
        "👀 Nothing logged today yet. Not judging — just checking you didn't forget.",
        "📭 Quiet day in the ledger so far. Spent anything, or is today an actual miracle?",
        "🧾 Haven't seen any spending from you today. Log it now, or it's gone forever (jk, but really).",
    )
    return body + "\n\n`/remind off` if you'd rather I didn't ask."


# -------------------------------------------------------------- check-ins

_PACE_TAGS = {
    "over": "🔺 running hot",
    "within": "✅ on track",
    "under": "🐢 comfortably under",
}


def checkin_header(checkin_type: str) -> str:
    label = "Mid-month" if checkin_type == "midmonth" else "Home stretch"
    return pick(
        f"📅 *{label} check-in* — let's see how the budgets are holding up.",
        f"📅 *{label} check-in.* No judgment, just numbers.",
    )


def checkin_header_ondemand() -> str:
    return "📅 *Budget pace check* (on demand — I didn't ask for this one, you did)."


def pace_line(category: str, pct_used: float, pct_elapsed: float, status: str) -> str:
    tag = _PACE_TAGS.get(status, "")
    return f"• *{category}*: {pct_used:.0f}% of budget used, {pct_elapsed:.0f}% of the month gone — {tag}"


def whatsnext_header() -> str:
    return pick(
        "🧭 *Here's what's actually worth doing next:*",
        "🧭 *Based on your recent history, here's what stands out:*",
    )


def whatsnext_pace_line(category: str, status: str, streak: int) -> str:
    direction = "over" if status == "over" else "under"
    return f"*{category}* has run {direction} pace for {streak} check-ins running — might be time to adjust it."


def whatsnext_all_caught_up() -> str:
    return pick(
        "Budgets set, thresholds set, nothing off-pace — honestly? Nothing urgent. `/summary` if you want to admire it.",
        "Can't find anything worth nagging you about right now. Suspicious, but I'll take it.",
    )


def whatsnext_nothing_yet() -> str:
    return "Nothing to suggest yet — log a purchase or two and check back."


def budget_nudge(category: str, status: str, streak: int) -> str:
    direction = "over" if status == "over" else "under"
    return (
        f"👋 *{category}* has run {direction} pace for {streak} check-ins in a row now — "
        f"might be worth adjusting the budget instead of relitigating it every time. Want to?"
    )
