"""Generic conversational engine for the setup commands (addcategory,
setbudget, setthreshold, addrecurring, addsub) when invoked with no
arguments. Instead of remembering exact command syntax, the bot asks one
question at a time — text for free-form values, buttons for anything from a
known set (categories, frequencies, yes/no-style choices).

State lives in context.user_data, scoped per Telegram user — fine for this
bot's single/few-person design. A flow is defined in bot/wizards.py as a
`get_fields(data) -> list[field]` function (recomputed each step, so later
fields can depend on earlier answers — see setthreshold's conditional
category step) plus an async `finalize(context, user_id, data) -> str`
that actually writes to the database and returns the confirmation text.

Field kinds: 'text', 'amount' (float > 0), 'amount_int' (int, min/max),
'date' (YYYY-MM-DD), 'choice' (buttons from a fixed list), 'category'
(buttons built live from the user's categories).
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import db, persona, timeutil

STATE_KEY = "wizard"


def is_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return context.user_data.get(STATE_KEY) is not None


async def start(
    update: Update, context: ContextTypes.DEFAULT_TYPE, flow_name: str, prefill: dict | None = None
) -> None:
    """Starts a conversational flow. `prefill` lets a caller that already
    knows some of the answers (e.g. a budget check-in nudge that already
    knows which category) seed them ahead of time — the wizard skips
    straight past those fields to the first one still unanswered, and
    finishes immediately if that turns out to be all of them."""
    from bot import wizards  # local import: wizards.py doesn't import this module, so no cycle

    flow = wizards.FLOWS[flow_name]
    data: dict = dict(prefill or {})
    fields = flow["get_fields"](data)
    step = 0
    while step < len(fields) and fields[step]["key"] in data:
        step += 1
        fields = flow["get_fields"](data)  # later fields can depend on earlier answers

    context.user_data[STATE_KEY] = {"flow": flow_name, "step": step, "data": data}

    if step >= len(fields):
        user_id = update.effective_user.id
        text = await flow["finalize"](context, user_id, data)
        context.user_data.pop(STATE_KEY, None)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="Markdown")
        return

    await _send_prompt(context, update.effective_chat.id, step, fields[step], update.effective_user.id)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_active(context):
        await update.message.reply_text(persona.wizard_no_active())
        return
    context.user_data.pop(STATE_KEY, None)
    await update.message.reply_text(persona.wizard_cancelled())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot import wizards

    state = context.user_data.get(STATE_KEY)
    if not state:
        return
    flow = wizards.FLOWS[state["flow"]]
    fields = flow["get_fields"](state["data"])
    field = fields[state["step"]]

    if field["kind"] not in ("text", "amount", "amount_int", "date"):
        await update.message.reply_text(
            "Tap one of the buttons above instead of typing — they're there for a reason."
        )
        return

    raw = (update.message.text or "").strip()
    value, error = _parse_value(field, raw)
    if error:
        await update.message.reply_text(error)
        return

    await _advance(update.effective_chat.id, context, state, field["key"], value, update.effective_user.id)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot import wizards

    query = update.callback_query
    await query.answer()
    state = context.user_data.get(STATE_KEY)
    if not state:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    _, step_str, raw_value = query.data.split(":", 2)
    step = int(step_str)
    if step != state["step"]:
        return  # stale button from an earlier prompt — ignore quietly

    await query.edit_message_reply_markup(reply_markup=None)

    if raw_value == "__cancel__":
        context.user_data.pop(STATE_KEY, None)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=persona.wizard_cancelled())
        return

    flow = wizards.FLOWS[state["flow"]]
    fields = flow["get_fields"](state["data"])
    field = fields[state["step"]]
    value = int(raw_value) if field["kind"] == "category" else raw_value

    await _advance(update.effective_chat.id, context, state, field["key"], value, update.effective_user.id)


async def _advance(chat_id: int, context: ContextTypes.DEFAULT_TYPE, state: dict, key: str, value, user_id: int) -> None:
    from bot import wizards

    state["data"][key] = value
    state["step"] += 1
    flow = wizards.FLOWS[state["flow"]]
    fields = flow["get_fields"](state["data"])

    if state["step"] >= len(fields):
        text = await flow["finalize"](context, user_id, state["data"])
        context.user_data.pop(STATE_KEY, None)
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    else:
        await _send_prompt(context, chat_id, state["step"], fields[state["step"]], user_id)


async def _send_prompt(context: ContextTypes.DEFAULT_TYPE, chat_id: int, step_idx: int, field: dict, user_id: int) -> None:
    markup = _build_markup(step_idx, field, user_id)
    await context.bot.send_message(chat_id=chat_id, text=field["prompt"], parse_mode="Markdown", reply_markup=markup)


def _build_markup(step_idx: int, field: dict, user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if field["kind"] == "choice":
        for label, value in field["choices"]:
            rows.append([InlineKeyboardButton(label, callback_data=f"wiz:{step_idx}:{value}")])
    elif field["kind"] == "category":
        cats = db.list_categories(user_id)
        row: list[InlineKeyboardButton] = []
        for i, cat in enumerate(cats, start=1):
            row.append(InlineKeyboardButton(cat["name"], callback_data=f"wiz:{step_idx}:{cat['id']}"))
            if i % 2 == 0:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([InlineKeyboardButton("✖ Cancel", callback_data=f"wiz:{step_idx}:__cancel__")])
    return InlineKeyboardMarkup(rows)


def _parse_value(field: dict, raw: str):
    kind = field["kind"]
    if kind == "text":
        if not raw:
            return None, "Can't be blank — give it a name."
        return raw[:60], None

    if kind == "amount":
        try:
            amount = round(float(raw.replace(",", ".")), 2)
        except ValueError:
            return None, persona.bad_amount()
        if amount <= 0:
            return None, "Needs to be greater than zero — nice try though."
        return amount, None

    if kind == "amount_int":
        try:
            n = int(raw)
        except ValueError:
            return None, "That needs to be a whole number."
        lo, hi = field.get("min", 1), field.get("max", 300)
        if not (lo <= n <= hi):
            return None, f"Keep it between {lo} and {hi}."
        return n, None

    if kind == "date":
        try:
            return timeutil.parse_date(raw).isoformat(), None
        except ValueError:
            return None, "That doesn't look like a date — use YYYY-MM-DD, e.g. 2026-09-05."

    return raw, None
