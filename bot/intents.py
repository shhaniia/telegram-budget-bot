"""Lightweight natural-language routing for plain-text messages that read
like a question or request rather than a purchase log ('what did I spend
today?' vs '12.50 lunch'). Each intent is a regex plus a small async
runner; the first pattern that matches wins.

This is deliberately not a real NLU model — just enough pattern-matching
on common phrasings to save people from memorizing command names, while
still telling them the actual command so they can skip the round-trip
next time. purchases.text_message calls try_route() before falling back
to its own amount-in-text purchase-log parsing, so a natural-language
match always takes priority over "is there a number in here somewhere".
"""
from __future__ import annotations

import re  # noqa: E402 (kept first among stdlib imports below)

from telegram import Update
from telegram.ext import ContextTypes

from bot import wizard
from bot.handlers import budgets, categories, general, purchases, recurring, reports, subscriptions


async def _hinted(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str, runner) -> None:
    await update.message.reply_text(f"👉 That sounds like `{command}` — here you go:", parse_mode="Markdown")
    await runner(update, context)


async def _hinted_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str, flow: str) -> None:
    await update.message.reply_text(
        f"👉 That's what `{command}` is for — let's do it conversationally:", parse_mode="Markdown"
    )
    await wizard.start(update, context, flow)


async def _hinted_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str) -> None:
    from bot import checkins  # local import: checkins imports wizard, avoid any load-order surprises

    await _hinted(update, context, command, checkins.checkin_cmd)


async def _hinted_whatsnext(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str) -> None:
    from bot import suggestions  # local import: suggestions imports checkins/wizard, avoid load-order surprises

    await _hinted(update, context, command, suggestions.whatsnext_cmd)


async def _direct(update: Update, context: ContextTypes.DEFAULT_TYPE, runner) -> None:
    """Like _hinted, but skips the '👉 that sounds like...' preamble —
    for intents (like deleting a purchase) whose own reply already gives
    full context and doesn't map to one fixed command name."""
    await runner(update, context)


def _summary_for(period_arg: str):
    async def _run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.args = [period_arg]
        await reports.summary_cmd(update, context)

    return _run


_SPEND = r"spen(?:d|t|ding)"

# (pattern, command shown to the user, kind, target)
# kind: "handler"   -> target is an async (update, context) callable, called directly
#       "direct"    -> like "handler" but skips the "sounds like <command>" preamble
#       "wizard"    -> target is a flow name, started conversationally
#       "checkin"   -> target unused, routes to /checkin
#       "whatsnext" -> target unused, routes to /whatsnext
_INTENTS: list[tuple[re.Pattern, str, str, object]] = [
    (
        # Checked first and deliberately broad — "remove/discard/delete/undo"
        # anywhere in the message almost always means "get rid of a logged
        # purchase", and this has to win over the "recent purchases" pattern
        # below (which "delete my last log" would otherwise also match on
        # "last" + "log"). Excludes messages that also contain a question
        # word, so "how do I discard a wrong entry" still falls through to
        # a normal answer instead of trying to delete something.
        re.compile(r"^(?!.*\b(how|what|why)\b).*\b(remove|discard|delete|erase|scrap|undo)\b", re.I),
        "delete a purchase",
        "direct",
        purchases.delete_purchase_intent,
    ),
    (
        re.compile(
            r"\bwhat'?s?\s+next\b|\bwhat should i do\b|\bany suggestions\b|\bwhat are my options\b",
            re.I,
        ),
        "/whatsnext",
        "whatsnext",
        None,
    ),
    (
        re.compile(rf"\b{_SPEND}\b.{{0,20}}\btoday\b|\btoday\b.{{0,20}}\b{_SPEND}\b", re.I),
        "/recent",
        "handler",
        purchases.recent_cmd,
    ),
    (
        re.compile(r"\b(recent|last|latest)\b.{0,10}\b(purchase|transaction|expense|log)s?\b", re.I),
        "/recent",
        "handler",
        purchases.recent_cmd,
    ),
    (
        re.compile(rf"\b{_SPEND}\b.{{0,20}}\bweek\b|\bthis week\b.{{0,20}}\b{_SPEND}\b", re.I),
        "/summary week",
        "handler",
        _summary_for("week"),
    ),
    (
        re.compile(rf"\b{_SPEND}\b.{{0,20}}\byear\b|\bthis year\b.{{0,20}}\b{_SPEND}\b", re.I),
        "/summary year",
        "handler",
        _summary_for("year"),
    ),
    (
        re.compile(
            rf"\bspending breakdown\b|\bhow am i doing\b|\bmonthly summary\b|"
            rf"\b{_SPEND}\b.{{0,25}}\bmonth\b|\bthis month\b.{{0,20}}\b{_SPEND}\b",
            re.I,
        ),
        "/summary",
        "handler",
        reports.summary_cmd,
    ),
    (
        re.compile(
            r"\bhow.{0,15}budgets?\b.{0,15}\b(doing|looking)\b|\bbudget status\b|"
            r"\bover budget\b|\bhow much.{0,15}\b(left|remaining)\b",
            re.I,
        ),
        "/budgets",
        "handler",
        budgets.budgets_cmd,
    ),
    (
        re.compile(r"\bam i (on track|pacing)\b|\bhow.{0,10}i (pacing|tracking)\b|\bcheck.?in\b", re.I),
        "/checkin",
        "checkin",
        None,
    ),
    (
        re.compile(r"\b(what|list|show).{0,15}\bcategories\b", re.I),
        "/categories",
        "handler",
        categories.categories_cmd,
    ),
    (
        re.compile(r"\badd\b.{0,10}\bcategory\b|\bnew categor", re.I),
        "/addcategory",
        "wizard",
        "addcategory",
    ),
    (
        re.compile(r"\bset\b.{0,10}\b(a |my )?budget\b|\b(update|change)\b.{0,10}\bbudget\b", re.I),
        "/setbudget",
        "wizard",
        "setbudget",
    ),
    (
        re.compile(r"\b(set|change)\b.{0,15}\b(alert|threshold)\b", re.I),
        "/setthreshold",
        "wizard",
        "setthreshold",
    ),
    (
        re.compile(r"\b(what|list|show).{0,20}\brecurring\b", re.I),
        "/recurring",
        "handler",
        recurring.recurring_cmd,
    ),
    (
        re.compile(r"\badd\b.{0,15}\brecurring\b", re.I),
        "/addrecurring",
        "wizard",
        "addrecurring",
    ),
    (
        re.compile(r"\b(what|list|show).{0,20}\bsubscriptions?\b", re.I),
        "/subs",
        "handler",
        subscriptions.subs_cmd,
    ),
    (
        re.compile(r"\badd\b.{0,15}\bsubscription\b|\btrack\b.{0,15}\bsubscription\b", re.I),
        "/addsub",
        "wizard",
        "addsub",
    ),
    (
        re.compile(r"\bremind me\b.{0,15}\blog\b|\b(turn|switch)\b.{0,5}\bon\b.{0,15}\breminders?\b", re.I),
        "/remind on",
        "handler",
        None,  # filled in below to avoid an import cycle at module load
    ),
    (
        re.compile(r"\bstop remind\w*\b|\b(turn|switch)\b.{0,5}\boff\b.{0,15}\breminders?\b", re.I),
        "/remind off",
        "handler",
        None,
    ),
    (
        re.compile(r"\bhelp\b|\bwhat can you do\b|\bhow does this work\b|\bcommands\b", re.I),
        "/help",
        "handler",
        general.help_cmd,
    ),
]


async def _remind_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.handlers import settings

    context.args = ["on"]
    await settings.remind_cmd(update, context)


async def _remind_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.handlers import settings

    context.args = ["off"]
    await settings.remind_cmd(update, context)


# Patch in the two remind-toggle targets now that the helpers above exist.
for _i, (_pattern, _cmd, _kind, _target) in enumerate(_INTENTS):
    if _cmd == "/remind on":
        _INTENTS[_i] = (_pattern, _cmd, _kind, _remind_on)
    elif _cmd == "/remind off":
        _INTENTS[_i] = (_pattern, _cmd, _kind, _remind_off)


async def try_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Attempts to match the message text to a known intent and act on it.
    Returns True if it handled the message — the caller should stop there
    rather than also trying to parse it as a purchase log."""
    text = update.message.text or ""
    for pattern, command, kind, target in _INTENTS:
        if not pattern.search(text):
            continue
        if kind == "handler":
            await _hinted(update, context, command, target)
        elif kind == "direct":
            await _direct(update, context, target)
        elif kind == "wizard":
            await _hinted_wizard(update, context, command, target)
        elif kind == "checkin":
            await _hinted_checkin(update, context, command)
        elif kind == "whatsnext":
            await _hinted_whatsnext(update, context, command)
        return True
    return False
