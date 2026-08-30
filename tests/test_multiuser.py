"""Verifies multi-tenant data isolation: two different Telegram user_ids
must get fully independent categories, budgets, and purchases, with no
leakage between them. This is the core guarantee that makes running the
bot in public mode (no allow-list) safe.
Run with: python tests/test_multiuser.py
"""
import os
import sys
import tempfile

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0000000000:TEST_TOKEN_NOT_REAL")
os.environ.setdefault("ALLOWED_USER_IDS", "")  # public mode
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ.setdefault("TIMEZONE", "Asia/Singapore")
os.environ.setdefault("DAILY_CHECK_TIME", "09:00")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config, db  # noqa: E402

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def main():
    check("public mode detected (empty allow-list)", config.PUBLIC_MODE is True)

    db.init_db()

    user_a = 111111
    user_b = 222222

    db.ensure_categories_seeded(user_a)
    db.ensure_categories_seeded(user_b)

    cats_a = db.list_categories(user_a)
    cats_b = db.list_categories(user_b)
    check("both users get default categories seeded", len(cats_a) == len(cats_b) == len(config.DEFAULT_CATEGORIES))

    # user A customizes their own category; must not appear for user B
    db.add_category(user_a, "Scuba Gear", is_necessity=False)
    check("user A's custom category exists for user A", db.get_category_by_name(user_a, "Scuba Gear") is not None)
    check("user A's custom category invisible to user B", db.get_category_by_name(user_b, "Scuba Gear") is None)

    # same category name, different owners -> two independent rows, no collision
    db.add_category(user_a, "Hobbies", is_necessity=False)
    db.add_category(user_b, "Hobbies", is_necessity=True)
    cat_a_hobbies = db.get_category_by_name(user_a, "Hobbies")
    cat_b_hobbies = db.get_category_by_name(user_b, "Hobbies")
    check("same-named category can exist for both users independently", cat_a_hobbies["id"] != cat_b_hobbies["id"])
    check("each user's copy keeps its own necessity flag", cat_a_hobbies["is_necessity"] == 0 and cat_b_hobbies["is_necessity"] == 1)

    # get_category_by_id must not leak another user's category
    check("user B cannot fetch user A's category by id", db.get_category_by_id(user_b, cat_a_hobbies["id"]) is None)
    check("user A can fetch their own category by id", db.get_category_by_id(user_a, cat_a_hobbies["id"]) is not None)

    # budgets and purchases stay isolated too
    food_a = db.get_category_by_name(user_a, "Food & Drink")
    food_b = db.get_category_by_name(user_b, "Food & Drink")
    db.set_budget(user_a, food_a["id"], 500, "monthly")
    db.set_budget(user_b, food_b["id"], 900, "monthly")
    budget_a = db.get_budget(user_a, food_a["id"], "monthly")
    budget_b = db.get_budget(user_b, food_b["id"], "monthly")
    check("user A and user B have independent budgets for the same category name", budget_a["amount"] == 500 and budget_b["amount"] == 900)

    db.add_purchase(user_a, 50, food_a["id"], "user A lunch", "manual")
    db.add_purchase(user_b, 70, food_b["id"], "user B lunch", "manual")
    spend_a = db.spend_for_category_period(user_a, food_a["id"], "monthly")
    spend_b = db.spend_for_category_period(user_b, food_b["id"], "monthly")
    check("user A's spend doesn't include user B's purchase", abs(spend_a - 50) < 0.001)
    check("user B's spend doesn't include user A's purchase", abs(spend_b - 70) < 0.001)

    recent_a = db.recent_purchases(user_a, limit=50)
    check("user A's recent purchases contain only their own entries", all("user A" in p["description"] for p in recent_a))

    # free-text keyword matching against a custom category is scoped too
    match_for_a = db.find_category_in_text(user_a, "scuba gear trip")
    match_for_b = db.find_category_in_text(user_b, "scuba gear trip")
    check("keyword match on user A's custom category works for user A", match_for_a is not None and match_for_a["name"] == "Scuba Gear")
    check("same text finds nothing custom for user B (no Scuba Gear category)", match_for_b is None or match_for_b["name"] != "Scuba Gear")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        sys.exit(1)
    print("All multi-user isolation checks passed.")


if __name__ == "__main__":
    main()
