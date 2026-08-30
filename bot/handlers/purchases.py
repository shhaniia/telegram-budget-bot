from __future__ import annotations

import re
from itertools import groupby

from telegram import Update
from telegram.ext import ContextTypes

from bot import db, keyboards, ocr, persona, timeutil
from bot import wizard
from bot.alerts import check_and_send_threshold_alerts

_AMOUNT_TOKEN_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)")
# Currency symbols/shorthand that can end up sitting right next to where the
# amount was stripped out of free text (e.g. "S$10 on shopping" ->
# "S$" + " on shopping") — cleaned out of the description so it doesn't
# show up as a stray "$" with the amount missing.
_CURRENCY_JUNK_RE = re.compile(r"(?i)\bs\$|[$€£¥]")

# Words stripped out when turning "remove the $5 for lunch" into a search
# keyword ("lunch") to match against a purchase's description/category.
_DELETE_FILLER_RE = re.compile(
    r"\b(remove|discard|delete|erase|scrap|undo|cancel|the|that|this|my|an?|entry|entries|log|logs|"
    r"purchase|purchases|expense|expenses|transaction|transactions|item|last|latest|recent|"
    r"for|of|please|pls)\b",
    re.I,
)
_MOST_RECENT_RE = re.compile(r"\b(last|latest|most recent)\b", re.I)


def _budget_line(user_id: int, category_row) -> str:
    budget = db.get_budget(user_id, category_row["id"], "monthly")
    if not budget:
        return ""
    spent = db.spend_for_category_period(user_id, category_row["id"], "monthly")
    pct = (spent / budget["amount"] * 100) if budget["amount"] else 0
    return f"\n{category_row['name']} this month: ${spent:,.2f} / ${budget['amount']:,.2f} ({pct:.0f}%)"


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/log 12.50 Food lunch with the team"""
    if not context.args:
        await update.message.reply_text(
            "Usage: `/log 12.50 Food lunch with the team`\n"
            "Or just send `12.50 lunch` like a normal message — I'm not picky.",
            parse_mode="Markdown",
        )
        return
    await _handle_free_text(update, context, " ".join(context.args))


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any plain-text message that isn't a command. Priority order: an
    in-progress conversational flow gets first dibs, then natural-language
    intent routing ("what did I spend today?" -> /recent), then finally a
    best-effort quick purchase log parse."""
    if wizard.is_active(context):
        await wizard.handle_text(update, context)
        return

    from bot import intents  # local import: intents imports this module, avoids a load-order cycle

    if await intents.try_route(update, context):
        return

    text = update.message.text or ""
    match = _AMOUNT_TOKEN_RE.search(text)
    if not match:
        await update.message.reply_text(persona.no_amount_found())
        return
    await _handle_free_text(update, context, text)


async def _handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    match = _AMOUNT_TOKEN_RE.search(text)
    if not match:
        await update.message.reply_text(persona.no_amount_found())
        return

    amount_str = match.group(1).replace(",", ".")
    try:
        amount = round(float(amount_str), 2)
    except ValueError:
        await update.message.reply_text(persona.bad_amount())
        return
    if amount <= 0:
        await update.message.reply_text("Amount needs to be greater than zero — nice try though.")
        return

    remainder = text[: match.start()] + " " + text[match.end():]
    remainder = _CURRENCY_JUNK_RE.sub("", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip()
    category = db.find_category_in_text(user_id, remainder) if remainder else None
    description = remainder

    if category:
        purchase_id = db.add_purchase(
            user_id=user_id,
            amount=amount,
            category_id=category["id"],
            description=description,
            source="manual",
        )
        reply = persona.logged_line(amount, category["name"])
        if description:
            reply += f"\n_{description}_"
        reply += _budget_line(user_id, category)
        await update.message.reply_text(
            reply,
            parse_mode="Markdown",
            reply_markup=keyboards.log_actions(purchase_id),
        )
        await check_and_send_threshold_alerts(context, user_id, chat_id, category)
    else:
        pending_id = db.create_pending(
            user_id=user_id,
            chat_id=chat_id,
            amount=amount,
            category_id=None,
            description=description,
            source="manual",
        )
        await update.message.reply_text(
            persona.pending_amount_only(amount, description),
            parse_mode="Markdown",
            reply_markup=keyboards.category_picker("cat", pending_id, user_id),
        )


async def photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receipt photo or an Apple Pay / Apple Wallet notification screenshot."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    status_msg = await update.message.reply_text(persona.ocr_reading())
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    image_bytes = bytes(await tg_file.download_as_bytearray())

    try:
        text = ocr.extract_text(image_bytes)
        amount, description = ocr.guess_amount_and_description(text)
    except Exception:
        await status_msg.edit_text(persona.ocr_failed())
        return

    category = db.find_category_in_text(user_id, description) if description else None

    pending_id = db.create_pending(
        user_id=user_id,
        chat_id=chat_id,
        amount=amount,
        category_id=category["id"] if category else None,
        description=description or "",
        source="ocr",
    )

    if amount is None:
        await status_msg.edit_text(persona.ocr_no_amount(pending_id))
        return

    summary = persona.ocr_result(amount, description, category["name"] if category else None)

    if category:
        await status_msg.edit_text(
            summary,
            parse_mode="Markdown",
            reply_markup=keyboards.confirm_purchase(pending_id, category["name"]),
        )
    else:
        await status_msg.edit_text(
            summary,
            parse_mode="Markdown",
            reply_markup=keyboards.category_picker("cat", pending_id, user_id),
        )


async def fix_amount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/fix <pending_id> <amount> — correct an OCR misread before confirming."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/fix <id> <amount>`", parse_mode="Markdown")
        return
    try:
        pending_id = int(context.args[0])
        amount = round(float(context.args[1].replace(",", ".")), 2)
    except ValueError:
        await update.message.reply_text("Usage: `/fix <id> <amount>`", parse_mode="Markdown")
        return

    pending = db.get_pending(pending_id)
    if not pending or pending["user_id"] != update.effective_user.id:
        await update.message.reply_text(persona.entry_expired())
        return

    db.update_pending_amount(pending_id, amount)
    category = db.get_category_by_id(pending["user_id"], pending["category_id"]) if pending["category_id"] else None
    if category:
        await update.message.reply_text(
            f"Updated to ${amount:,.2f} — {category['name']}. Confirm?",
            reply_markup=keyboards.confirm_purchase(pending_id, category["name"]),
        )
    else:
        await update.message.reply_text(
            f"Updated to ${amount:,.2f}. Which category?",
            reply_markup=keyboards.category_picker("cat", pending_id, pending["user_id"]),
        )


async def category_picker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles taps on a `cat:<pending_id>:<category_id|cancel>` keyboard —
    used both for fresh manual entries and OCR results with no category guess."""
    query = update.callback_query
    await query.answer()
    _, pending_id_str, choice = query.data.split(":")
    pending_id = int(pending_id_str)
    pending = db.get_pending(pending_id)
    if not pending or pending["user_id"] != update.effective_user.id:
        await query.edit_message_text(persona.entry_expired())
        return

    if choice == "cancel":
        db.delete_pending(pending_id)
        await query.edit_message_text(persona.discarded())
        return

    category = db.get_category_by_id(pending["user_id"], int(choice))
    if not category or pending["amount"] is None:
        await query.edit_message_text("Something went sideways with that entry — just re-send it.")
        return

    purchase_id = db.add_purchase(
        user_id=pending["user_id"],
        amount=pending["amount"],
        category_id=category["id"],
        description=pending["description"] or "",
        source=pending["source"],
    )
    db.delete_pending(pending_id)

    reply = persona.logged_line(pending["amount"], category["name"])
    if pending["description"]:
        reply += f"\n_{pending['description']}_"
    reply += _budget_line(pending["user_id"], category)
    await query.edit_message_text(reply, parse_mode="Markdown", reply_markup=keyboards.log_actions(purchase_id))
    await check_and_send_threshold_alerts(
        context, pending["user_id"], pending["chat_id"], category
    )


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the Save / Change category / Discard buttons after an OCR read."""
    query = update.callback_query
    await query.answer()
    _, pending_id_str, action = query.data.split(":")
    pending_id = int(pending_id_str)
    pending = db.get_pending(pending_id)
    if not pending or pending["user_id"] != update.effective_user.id:
        await query.edit_message_text(persona.entry_expired())
        return

    if action == "cancel":
        db.delete_pending(pending_id)
        await query.edit_message_text(persona.discarded())
        return

    if action == "change":
        await query.edit_message_text(
            f"${pending['amount']:,.2f}" + (f" — {pending['description']}" if pending["description"] else "")
            + f"\n{persona.wrong_category_prompt()}",
            reply_markup=keyboards.category_picker("cat", pending_id, pending["user_id"]),
        )
        return

    if action == "save":
        category = db.get_category_by_id(pending["user_id"], pending["category_id"]) if pending["category_id"] else None
        if not category:
            await query.edit_message_text(
                "No category set — pick one.",
                reply_markup=keyboards.category_picker("cat", pending_id, pending["user_id"]),
            )
            return
        purchase_id = db.add_purchase(
            user_id=pending["user_id"],
            amount=pending["amount"],
            category_id=category["id"],
            description=pending["description"] or "",
            source=pending["source"],
        )
        db.delete_pending(pending_id)
        reply = persona.logged_line(pending["amount"], category["name"])
        if pending["description"]:
            reply += f"\n_{pending['description']}_"
        reply += _budget_line(pending["user_id"], category)
        await query.edit_message_text(reply, parse_mode="Markdown", reply_markup=keyboards.log_actions(purchase_id))
        await check_and_send_threshold_alerts(
            context, pending["user_id"], pending["chat_id"], category
        )


async def recategorize_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the compact 'Wrong category' button on an already-saved
    purchase — expands it into the full category grid in place."""
    query = update.callback_query
    await query.answer()
    _, purchase_id_str = query.data.split(":")
    purchase_id = int(purchase_id_str)
    user_id = update.effective_user.id
    await query.edit_message_reply_markup(reply_markup=keyboards.category_picker("recat", purchase_id, user_id))


async def recategorize_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the category-picker attached to an *already-saved* purchase
    (opened via the compact 'wrong category?' button)."""
    query = update.callback_query
    await query.answer()
    _, purchase_id_str, choice = query.data.split(":")
    purchase_id = int(purchase_id_str)
    user_id = update.effective_user.id

    if choice == "cancel":
        await query.edit_message_reply_markup(reply_markup=keyboards.log_actions(purchase_id))
        return

    category = db.get_category_by_id(user_id, int(choice))
    if not category:
        return
    ok = db.update_purchase_category(purchase_id, user_id, category["id"])
    if not ok:
        await query.edit_message_text(persona.entry_expired())
        return
    await query.edit_message_text(persona.recategorized(category["name"]), parse_mode="Markdown")


async def undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, purchase_id_str = query.data.split(":")
    purchase_id = int(purchase_id_str)
    ok = db.delete_purchase(purchase_id, update.effective_user.id)
    if ok:
        await query.edit_message_text(persona.undone())
    else:
        await query.edit_message_text(persona.already_gone())


def _label_for_date(date_iso: str) -> str:
    if date_iso == timeutil.today_iso():
        return "Today"
    if date_iso == timeutil.yesterday().isoformat():
        return "Yesterday"
    return date_iso


def _resolve_recent_date_arg(args) -> str | None:
    """Turns /recent's optional argument into a date (ISO string), or None
    if it doesn't parse. No argument, or 'today', means today; 'yesterday'
    means yesterday; anything else is tried as YYYY-MM-DD."""
    if not args:
        return timeutil.today_iso()
    arg = args[0].strip().lower()
    if arg == "today":
        return timeutil.today_iso()
    if arg == "yesterday":
        return timeutil.yesterday().isoformat()
    try:
        return timeutil.parse_date(args[0]).isoformat()
    except ValueError:
        return None


def _build_day_report(user_id: int, date_iso: str):
    """Returns (text, reply_markup) for every purchase logged on date_iso,
    grouped by category (in category > item order) with a day total and a
    per-category subtotal — or (None, None) if there's nothing that day."""
    rows = db.purchases_for_date(user_id, date_iso)
    if not rows:
        return None, None

    label = _label_for_date(date_iso)
    total = sum(r["amount"] for r in rows)
    header = f"*{label}'s spending" if label in ("Today", "Yesterday") else f"*Spending on {label}"
    lines = [f"{header} — ${total:,.2f}*", ""]

    for category_name, group in groupby(rows, key=lambda r: r["category_name"]):
        items = list(group)
        cat_total = sum(r["amount"] for r in items)
        lines.append(f"*{category_name}* — ${cat_total:,.2f}")
        for r in items:
            desc = f" — {r['description']}" if r["description"] else ""
            lines.append(f"  #{r['id']} ${r['amount']:,.2f}{desc}")
        lines.append("")

    lines.append(persona.recent_footer())
    text = "\n".join(lines).rstrip()
    markup = keyboards.discard_grid(rows, prefix="recentundo")
    return text, markup


async def recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/recent — today's purchases, grouped by category, with a day total.
    /recent yesterday, or /recent 2026-08-29, for a different day."""
    user_id = update.effective_user.id
    date_iso = _resolve_recent_date_arg(context.args)
    if date_iso is None:
        await update.message.reply_text(
            "That date doesn't look right — try `/recent`, `/recent yesterday`, or `/recent 2026-08-29`.",
            parse_mode="Markdown",
        )
        return

    text, markup = _build_day_report(user_id, date_iso)
    if text is None:
        await update.message.reply_text(persona.recent_empty(_label_for_date(date_iso)))
        return
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def recent_undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles a 🗑 tap from /recent's listing — discards that one purchase
    and redraws the rest of the day's listing in place, rather than wiping
    the whole message the way the single-purchase `undo:` callback does."""
    query = update.callback_query
    await query.answer()
    _, purchase_id_str = query.data.split(":")
    purchase_id = int(purchase_id_str)
    user_id = update.effective_user.id

    purchase = db.get_purchase(purchase_id, user_id)
    if not purchase:
        await query.answer(persona.already_gone(), show_alert=True)
        return

    date_iso = purchase["purchase_date"]
    db.delete_purchase(purchase_id, user_id)

    text, markup = _build_day_report(user_id, date_iso)
    if text is None:
        await query.edit_message_text(f"{persona.undone()} Nothing else logged {_label_for_date(date_iso).lower()}.")
        return
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


def _extract_delete_query(text: str) -> tuple[float | None, str]:
    """Pulls an (optional) amount and a search keyword out of a natural-
    language delete request like 'remove the $5 for lunch' -> (5.0, 'lunch')."""
    amount = None
    match = _AMOUNT_TOKEN_RE.search(text)
    if match:
        try:
            amount = round(float(match.group(1).replace(",", ".")), 2)
        except ValueError:
            amount = None
        text = text[: match.start()] + " " + text[match.end():]
    text = _CURRENCY_JUNK_RE.sub("", text)
    keyword = _DELETE_FILLER_RE.sub(" ", text)
    keyword = re.sub(r"\s+", " ", keyword).strip()
    return amount, keyword


def _find_delete_candidates(user_id: int, amount: float | None, keyword: str):
    rows = db.recent_purchases(user_id, limit=200)  # most-recent-first, wide enough net
    candidates = list(rows)
    if amount is not None:
        candidates = [r for r in candidates if abs(r["amount"] - amount) < 0.005]
    if keyword:
        kw = keyword.lower()
        candidates = [
            r
            for r in candidates
            if kw in (r["description"] or "").lower() or kw in r["category_name"].lower()
        ]
    return candidates


async def delete_purchase_intent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles a natural-language 'remove/discard/delete the $5 lunch
    thing' request — finds the matching purchase(s) by amount and/or
    description keyword and either deletes the one clear match or offers
    a picker when more than one purchase could be what was meant."""
    text = update.message.text or ""
    user_id = update.effective_user.id
    amount, keyword = _extract_delete_query(text)
    wants_most_recent = bool(_MOST_RECENT_RE.search(text)) and amount is None and not keyword

    if amount is None and not keyword and not wants_most_recent:
        await update.message.reply_text(persona.delete_needs_more_detail(), parse_mode="Markdown")
        return

    if wants_most_recent:
        candidates = list(db.recent_purchases(user_id, limit=1))
    else:
        candidates = _find_delete_candidates(user_id, amount, keyword)

    if not candidates:
        await update.message.reply_text(persona.delete_not_found(), parse_mode="Markdown")
        return

    if len(candidates) == 1:
        row = candidates[0]
        db.delete_purchase(row["id"], user_id)
        await update.message.reply_text(
            persona.delete_confirmed(row["amount"], row["category_name"], row["description"] or "")
        )
        return

    await update.message.reply_text(
        persona.delete_ambiguous(),
        reply_markup=keyboards.purchase_picker(candidates[:10], prefix="undo"),
    )


async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/undo <purchase id>` — see `/recent` for ids.", parse_mode="Markdown")
        return
    try:
        purchase_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: `/undo <purchase id>`", parse_mode="Markdown")
        return
    ok = db.delete_purchase(purchase_id, update.effective_user.id)
    await update.message.reply_text(persona.undone() if ok else persona.already_gone())
