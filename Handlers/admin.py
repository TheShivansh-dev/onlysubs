"""
Handlers/admin.py
Minimal admin surface. All admin management now lives in the web panel.
The only Telegram-side admin interaction left is the expiry card the daily
job posts into the admin group: Save (with a day-count chooser) / Remove now.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import html
from datetime import datetime, timedelta

from .config import ADMIN_USER_ID, get_admin_group_id
from .db import db_extend_registered_user, db_extend_paid_user
from .subscription import kick_and_deactivate


def log(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', errors='replace').decode('ascii'))


def _is_admin(update: Update) -> bool:
    chat = update.effective_chat
    if chat and chat.type == 'private':
        return update.effective_user.id == ADMIN_USER_ID
    return bool(chat and chat.id == get_admin_group_id())


def _save_days_keyboard(kind: str, user_id: int) -> InlineKeyboardMarkup:
    """Day-count chooser shown after the admin taps Save."""
    days = [5, 10, 15, 20, 30]
    row = [InlineKeyboardButton(f"{d}d", callback_data=f"exp_days_{kind}_{user_id}_{d}") for d in days]
    return InlineKeyboardMarkup([row,
        [InlineKeyboardButton("❌ Remove now", callback_data=f"exp_rem_{kind}_{user_id}")]])


# ─────────────────────────────────────────────
#  Expiry card callback  (pattern: ^exp_)
#  exp_save_<kind>_<uid>          → show day chooser
#  exp_days_<kind>_<uid>_<days>   → extend by <days>
#  exp_rem_<kind>_<uid>           → remove now
#  kind = 'r' (registered) | 'p' (paid)
# ─────────────────────────────────────────────

async def handle_expiry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(update):
        await query.answer("Admins only.", show_alert=True)
        return
    await query.answer()

    parts  = query.data.split('_')          # exp, action, kind, uid [, days]
    action = parts[1]
    kind   = parts[2]
    try:
        user_id = int(parts[3])
    except (IndexError, ValueError):
        return

    actor = query.from_user.username or query.from_user.first_name

    # ── Step 1: admin tapped Save → ask for how many days ──
    if action == "save":
        await query.edit_message_reply_markup(reply_markup=_save_days_keyboard(kind, user_id))
        return

    # ── Step 2: admin picked a day count → extend ──
    if action == "days":
        try:
            days = int(parts[4])
        except (IndexError, ValueError):
            return
        new_end = str(datetime.now().date() + timedelta(days=days))
        if kind == "r":
            db_extend_registered_user(user_id, new_end)
        else:
            db_extend_paid_user(user_id, new_end)
        await query.edit_message_text(
            f"✅ <b>Saved for {days} days.</b>\n\n"
            f"User <code>{user_id}</code> kept — access extended to {new_end}.\n"
            f"By: {html.escape(str(actor))}",
            parse_mode="HTML",
        )
        return

    # ── Remove now ──
    if action == "rem":
        kicked = await kick_and_deactivate(context, kind, user_id)
        note = "removed from group" if kicked else "marked removed (no group_id set, not kicked)"
        await query.edit_message_text(
            f"❌ <b>Removed.</b>\n\nUser <code>{user_id}</code> {note}.\n"
            f"By: {html.escape(str(actor))}",
            parse_mode="HTML",
        )
