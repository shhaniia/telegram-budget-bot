"""Reusable inline keyboards."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot import db


def category_picker(prefix: str, ref_id: int, user_id: int) -> InlineKeyboardMarkup:
    """Grid of category buttons, scoped to this user's own categories.
    `prefix` identifies which flow is asking (e.g. 'cat' for a
    pending-confirmation flow), `ref_id` is the row id of the pending
    record (or purchase id) the choice applies to."""
    cats = db.list_categories(user_id)
    buttons = []
    row = []
    for i, cat in enumerate(cats, start=1):
        row.append(InlineKeyboardButton(cat["name"], callback_data=f"{prefix}:{ref_id}:{cat['id']}"))
        if i % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✖ Cancel", callback_data=f"{prefix}:{ref_id}:cancel")])
    return InlineKeyboardMarkup(buttons)


def confirm_purchase(pending_id: int, category_name: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(f"✅ Save as {category_name}", callback_data=f"confirm:{pending_id}:save"),
            InlineKeyboardButton("📂 Change category", callback_data=f"confirm:{pending_id}:change"),
        ],
        [InlineKeyboardButton("✖ Discard", callback_data=f"confirm:{pending_id}:cancel")],
    ]
    return InlineKeyboardMarkup(buttons)


def undo_purchase(purchase_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("↩ Undo this entry", callback_data=f"undo:{purchase_id}")]]
    )


def log_actions(purchase_id: int) -> InlineKeyboardMarkup:
    """Compact pair of actions attached to every logged purchase — swap the
    category if it guessed wrong, or wipe the entry entirely if it shouldn't
    exist at all. Tapping 'wrong category' expands into the full category
    grid (category_picker with prefix 'recat') in place."""
    buttons = [
        [
            InlineKeyboardButton("📂 Wrong category", callback_data=f"recatopen:{purchase_id}"),
            InlineKeyboardButton("🗑 Discard", callback_data=f"undo:{purchase_id}"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def discard_grid(purchases, prefix: str = "recentundo") -> InlineKeyboardMarkup:
    """Grid of quick-discard buttons, two per row, one per purchase — lets a
    multi-item listing (like /recent) be cleaned up with a tap instead of
    typing `/undo <id>`. `prefix` controls which callback handler picks up
    the tap; 'recentundo' rebuilds the whole listing in place afterward."""
    buttons = []
    row = []
    for i, p in enumerate(purchases, start=1):
        row.append(
            InlineKeyboardButton(f"🗑 #{p['id']} ${p['amount']:,.2f}", callback_data=f"{prefix}:{p['id']}")
        )
        if i % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def purchase_picker(purchases, prefix: str = "undo") -> InlineKeyboardMarkup:
    """One button per purchase, each labelled with its amount, category,
    and a hint of the description — used to disambiguate when a
    natural-language delete request ('remove the $5 lunch thing') matches
    more than one purchase and we need the user to point at the right one."""
    buttons = []
    for p in purchases:
        desc = f" {p['description']}" if p["description"] else ""
        label = f"${p['amount']:,.2f} · {p['category_name']}{desc}"
        if len(label) > 60:
            label = label[:57] + "..."
        buttons.append([InlineKeyboardButton(f"🗑 {label}", callback_data=f"{prefix}:{p['id']}")])
    return InlineKeyboardMarkup(buttons)


def subscription_reminder(sub_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("✅ Keep it", callback_data=f"sub:{sub_id}:keep"),
            InlineKeyboardButton("❌ Cancel it", callback_data=f"sub:{sub_id}:cancel"),
        ],
        [InlineKeyboardButton("⏰ Remind me tomorrow", callback_data=f"sub:{sub_id}:snooze")],
    ]
    return InlineKeyboardMarkup(buttons)
