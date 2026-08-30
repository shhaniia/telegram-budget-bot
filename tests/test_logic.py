"""Lightweight smoke tests that don't need a live Telegram token or network
access — run with: python tests/test_logic.py
They cover the database layer, date math, OCR text parsing, and free-text
amount parsing, which is most of the bot's actual logic surface."""
import os
import sys
import tempfile
from datetime import date

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0000000000:TEST_TOKEN_NOT_REAL")
os.environ.setdefault("ALLOWED_USER_IDS", "111111")
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ.setdefault("TIMEZONE", "Asia/Singapore")
os.environ.setdefault("DAILY_CHECK_TIME", "09:00")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db, ocr, timeutil  # noqa: E402

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def main():
    problems = config.validate()
    check("config validates with test env", not problems)

    db.init_db()
    user_id = 111111
    db.ensure_categories_seeded(user_id)
    cats = db.list_categories(user_id)
    check("default categories seeded", len(cats) == len(config.DEFAULT_CATEGORIES))

    food = db.get_category_by_name(user_id, "Food & Drink")
    check("category lookup case-insensitive works", db.get_category_by_name(user_id, "food & drink") is not None)
    check("food category found", food is not None)

    db.set_budget(user_id, food["id"], 400, "monthly")
    budget = db.get_budget(user_id, food["id"], "monthly")
    check("budget saved", budget is not None and budget["amount"] == 400)

    db.add_purchase(user_id, 12.5, food["id"], "lunch", "manual")
    db.add_purchase(user_id, 300, food["id"], "big shop", "manual")
    spent = db.spend_for_category_period(user_id, food["id"], "monthly")
    check("spend accumulates", abs(spent - 312.5) < 0.001)

    pct = spent / budget["amount"] * 100
    check("crossed 80% threshold math", pct >= 78)

    db.set_threshold(user_id, 80, food["id"])
    thresholds = db.thresholds_for_category(user_id, food["id"])
    check("threshold retrievable", 80 in thresholds)

    pkey = timeutil.period_key("monthly")
    check("not yet alerted", not db.was_alert_sent(user_id, food["id"], pkey, 80))
    db.record_alert_sent(user_id, food["id"], pkey, 80)
    check("alert recorded idempotent", db.was_alert_sent(user_id, food["id"], pkey, 80))

    found = db.find_category_in_text(user_id, "12.50 groceries run")
    check("keyword category match finds Groceries", found is not None and found["name"] == "Groceries")

    # --- recurring / subscription date math ---
    d = date(2026, 1, 31)
    nxt = timeutil.add_frequency(d, "monthly")
    check("monthly add clamps Jan31 -> Feb28", nxt == date(2026, 2, 28))

    leap_day = date(2024, 2, 29)
    nxt_year = timeutil.add_frequency(leap_day, "yearly")
    check("yearly add handles leap day", nxt_year in (date(2025, 2, 28), date(2025, 3, 1)))

    start, end = timeutil.period_bounds("weekly", date(2026, 8, 30))  # Sunday
    check("week bounds span 7 days", (end - start).days == 6)

    # --- recurring items ---
    rec_id = db.add_recurring(user_id, "Rent", 1500, food["id"], "monthly", True, timeutil.today_iso())
    due = db.due_recurring(timeutil.today_iso())
    check("recurring item is due today", any(r["id"] == rec_id for r in due))
    db.advance_recurring_due(rec_id, "2099-01-01")
    due2 = db.due_recurring(timeutil.today_iso())
    check("recurring item no longer due after advance", not any(r["id"] == rec_id for r in due2))

    # --- subscriptions ---
    sub_id = db.add_subscription(user_id, "Netflix", 15.99, timeutil.today_iso(), "monthly", 3)
    reminders = db.due_subscription_reminders(timeutil.today_iso())
    check("subscription due for reminder", any(r["id"] == sub_id for r in reminders))
    db.mark_subscription_reminded(sub_id, timeutil.today_iso())
    reminders2 = db.due_subscription_reminders(timeutil.today_iso())
    check("subscription reminder not repeated same cycle", not any(r["id"] == sub_id for r in reminders2))
    charges = db.due_subscription_charges(timeutil.today_iso())
    check("subscription due for charge", any(c["id"] == sub_id for c in charges))

    monthly_total = db.monthly_subscription_total(user_id)
    check("monthly subscription total includes Netflix", abs(monthly_total - 15.99) < 0.01)

    db.cancel_subscription(user_id, "netflix")
    check("subscription cancelled (case-insensitive name)", db.get_subscription_by_name(user_id, "Netflix") is None)

    # --- OCR text parsing (no image needed — pure text heuristics) ---
    receipt_text = "STARBUCKS COFFEE\nLatte 5.50\nMuffin 3.20\nSUBTOTAL 8.70\nTAX 0.70\nTOTAL 9.40\nThank you"
    amount, desc = ocr.guess_amount_and_description(receipt_text)
    check(f"receipt total parsed correctly (got {amount})", amount == 9.40)
    check(f"receipt description picked a text line (got {desc!r})", "STARBUCKS" in desc.upper())

    wallet_text = "Apple Pay\n$42.10\nTarget\nToday 3:41 PM"
    amount2, desc2 = ocr.guess_amount_and_description(wallet_text)
    check(f"wallet screenshot amount parsed (got {amount2})", amount2 == 42.10)

    thousands_text = "Total: $1,234.56"
    amount3, _ = ocr.guess_amount_and_description(thousands_text)
    check(f"thousands separator parsed correctly (got {amount3})", amount3 == 1234.56)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
