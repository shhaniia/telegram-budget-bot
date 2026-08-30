"""Flow definitions for the conversational setup wizards. Each flow is a
`get_fields(data) -> list[field]` function (see bot/wizard.py for field
kinds) plus an async `finalize(context, user_id, data) -> str` that writes
to the database and returns the confirmation message.
"""
from __future__ import annotations

from bot import db, persona, timeutil


# ------------------------------------------------------------- addcategory

def _addcategory_fields(data):
    return [
        {"key": "name", "kind": "text", "prompt": "What should I call this category? (e.g. 'Pets', 'Coffee')"},
        {
            "key": "necessity",
            "kind": "choice",
            "prompt": "Necessity, or more of a treat?",
            "choices": [("🔒 Necessity", "necessity"), ("✨ Treat / optional", "optional")],
        },
    ]


async def _finish_addcategory(context, user_id, data):
    name = data["name"]
    is_necessity = data["necessity"] == "necessity"
    if db.get_category_by_name(user_id, name):
        return persona.category_exists(name)
    db.add_category(user_id, name, is_necessity)
    return persona.category_added(name, is_necessity)


# ---------------------------------------------------------------- setbudget

def _setbudget_fields(data):
    return [
        {"key": "category_id", "kind": "category", "prompt": "Which category are we budgeting?"},
        {"key": "amount", "kind": "amount", "prompt": "How much per period? Just the number, no dollar sign needed."},
        {
            "key": "period",
            "kind": "choice",
            "prompt": "Monthly or weekly?",
            "choices": [("📅 Monthly", "monthly"), ("🗓 Weekly", "weekly")],
        },
    ]


async def _finish_setbudget(context, user_id, data):
    category = db.get_category_by_id(user_id, data["category_id"])
    db.set_budget(user_id, category["id"], data["amount"], data["period"])
    return persona.budget_set(category["name"], data["amount"], data["period"])


# ------------------------------------------------------------- setthreshold

def _setthreshold_fields(data):
    fields = [
        {
            "key": "percent",
            "kind": "amount_int",
            "min": 1,
            "max": 300,
            "prompt": "What percent should trigger an alert? (e.g. 80 for 80%)",
        },
        {
            "key": "scope",
            "kind": "choice",
            "prompt": "Apply this everywhere, or just one category?",
            "choices": [("🌍 Everywhere", "all"), ("🎯 One category", "one")],
        },
    ]
    if data.get("scope") == "one":
        fields.append({"key": "category_id", "kind": "category", "prompt": "Which one?"})
    return fields


async def _finish_setthreshold(context, user_id, data):
    category_id = data.get("category_id") if data.get("scope") == "one" else None
    db.set_threshold(user_id, data["percent"], category_id)
    scope_label = f"'{db.get_category_by_id(user_id, category_id)['name']}'" if category_id else "all categories"
    return persona.threshold_set(data["percent"], scope_label)


# ------------------------------------------------------------- addrecurring

def _addrecurring_fields(data):
    return [
        {"key": "name", "kind": "text", "prompt": "What's this recurring purchase called? (e.g. 'Rent')"},
        {"key": "amount", "kind": "amount", "prompt": "How much, each time?"},
        {"key": "category_id", "kind": "category", "prompt": "Which category?"},
        {
            "key": "frequency",
            "kind": "choice",
            "prompt": "How often?",
            "choices": [("🗓 Weekly", "weekly"), ("📅 Monthly", "monthly"), ("📆 Yearly", "yearly")],
        },
        {
            "key": "necessity",
            "kind": "choice",
            "prompt": "Necessity, or nice-to-have?",
            "choices": [("🔒 Necessity", "necessity"), ("✨ Nice-to-have", "optional")],
        },
    ]


async def _finish_addrecurring(context, user_id, data):
    category = db.get_category_by_id(user_id, data["category_id"])
    next_due = timeutil.add_frequency(timeutil.today(), data["frequency"]).isoformat()
    db.add_recurring(
        user_id=user_id,
        name=data["name"],
        amount=data["amount"],
        category_id=category["id"],
        frequency=data["frequency"],
        is_necessity=data["necessity"] == "necessity",
        next_due=next_due,
    )
    return persona.recurring_added(data["name"], data["amount"], data["frequency"], category["name"], next_due)


# ------------------------------------------------------------------ addsub

def _addsub_fields(data):
    return [
        {"key": "name", "kind": "text", "prompt": "What's the subscription called? (e.g. 'Netflix')"},
        {"key": "amount", "kind": "amount", "prompt": "How much does it cost each time?"},
        {"key": "next_billing_date", "kind": "date", "prompt": "When's the next charge? (YYYY-MM-DD, e.g. 2026-09-05)"},
        {
            "key": "frequency",
            "kind": "choice",
            "prompt": "How often does it bill?",
            "choices": [("📅 Monthly", "monthly"), ("📆 Yearly", "yearly"), ("🗓 Weekly", "weekly")],
        },
        {
            "key": "reminder_days",
            "kind": "choice",
            "prompt": "How many days before renewal should I warn you?",
            "choices": [("1 day before", "1"), ("3 days before", "3"), ("7 days before", "7")],
        },
    ]


async def _finish_addsub(context, user_id, data):
    reminder_days = int(data["reminder_days"])
    db.add_subscription(
        user_id=user_id,
        name=data["name"],
        amount=data["amount"],
        next_billing_date=data["next_billing_date"],
        frequency=data["frequency"],
        reminder_days_before=reminder_days,
    )
    return persona.sub_added(data["name"], data["amount"], data["frequency"], data["next_billing_date"], reminder_days)


FLOWS = {
    "addcategory": {"get_fields": _addcategory_fields, "finalize": _finish_addcategory},
    "setbudget": {"get_fields": _setbudget_fields, "finalize": _finish_setbudget},
    "setthreshold": {"get_fields": _setthreshold_fields, "finalize": _finish_setthreshold},
    "addrecurring": {"get_fields": _addrecurring_fields, "finalize": _finish_addrecurring},
    "addsub": {"get_fields": _addsub_fields, "finalize": _finish_addsub},
}
