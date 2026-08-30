from telegram import Update
from telegram.ext import ContextTypes

from bot import db, persona, wizard


async def categories_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cats = db.list_categories(update.effective_user.id)
    lines = ["*Your categories:*"]
    for c in cats:
        tag = " _(necessity)_" if c["is_necessity"] else ""
        lines.append(f"• {c['name']}{tag}")
    lines.append("\nAdd one with `/addcategory` — I'll ask the questions, you just answer.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def add_category_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await wizard.start(update, context, "addcategory")
        return

    is_necessity = False
    if args[-1].lower() in ("necessity", "necessary"):
        is_necessity = True
        args = args[:-1]
    name = " ".join(args).strip()
    if not name:
        await update.message.reply_text("Please give the category a name.")
        return

    if db.get_category_by_name(user_id, name):
        await update.message.reply_text(persona.category_exists(name))
        return

    db.add_category(user_id, name, is_necessity)
    await update.message.reply_text(persona.category_added(name, is_necessity), parse_mode="Markdown")
