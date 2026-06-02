from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .config import (
    ADMIN_USER_ID,
    SUPPORT_CONTACT,
    BOT_USERNAME,
    REGISTERED_USERS_FILE,
    get_admin_group_id,
    get_current_admin_group,
    set_admin_group_id,
    remove_admin_group,
    load_courses,
    save_course,
    delete_course,
    save_group,
    get_group_for_course,
    save_pending_invite,
)
from .db import (
    db_get_all_admin_users,
    db_create_admin_user,
    db_remove_admin_user,
    db_change_admin_password,
)
import os, html
import pandas as pd
from datetime import datetime
from .auth import hash_password as _hash_password

# ── in-memory conversation state per user ──
_state: dict[int, dict] = {}

# ── pending subgroup setup: chat_id waiting for course selection ──
_pending_subgroup: dict[int, int] = {}   # admin_user_id → group_chat_id


def _is_admin(update: Update) -> bool:
    chat = update.effective_chat
    print(update.effective_user.id)
    if chat.type == 'private':
        return update.effective_user.id == ADMIN_USER_ID
    return chat.id == get_admin_group_id()


def _bot_link(payload: str) -> str:
    return f"https://t.me/{BOT_USERNAME.lstrip('@')}?startgroup={payload}"


# ─────────────────────────────────────────────
#  KEYBOARDS
# ─────────────────────────────────────────────

def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Set Admin Group", callback_data="adm_setadmin"),
         InlineKeyboardButton("Add Sub Group",   callback_data="adm_addgroup")],
        [InlineKeyboardButton("Add Course",      callback_data="adm_addcourse"),
         InlineKeyboardButton("Delete Course",   callback_data="adm_delcourse")],
        [InlineKeyboardButton("Add User",        callback_data="adm_adduser"),
         InlineKeyboardButton("See Users",       callback_data="adm_seeusers")],
        [InlineKeyboardButton("All Users",       callback_data="adm_allusers")],
    ])


def _admin_group_set_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Remove Admin Group", callback_data="adm_removeadmin")],
        [InlineKeyboardButton("Back",                  callback_data="adm_back")],
    ])


def _two_option_keyboard(via_cb: str, manual_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Via Bot Link", callback_data=via_cb),
         InlineKeyboardButton("Enter ID",     callback_data=manual_cb)],
        [InlineKeyboardButton("Back",         callback_data="adm_back")],
    ])


def _delete_course_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    courses  = load_courses()
    per_page = 5
    total    = len(courses)
    start    = page * per_page
    end      = min(start + per_page, total)
    buttons  = [
        [
            InlineKeyboardButton(str(r['course_name']), callback_data=f"adm_dc_n_{int(r['id'])}"),
            InlineKeyboardButton("🗑",                  callback_data=f"adm_dc_{int(r['id'])}_{page}"),
        ]
        for _, r in courses.iloc[start:end].iterrows()
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"adm_dc_pg_{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"adm_dc_pg_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("Back", callback_data="adm_back")])
    return InlineKeyboardMarkup(buttons)


def _adduser_course_keyboard(courses_df, page: int = 0) -> InlineKeyboardMarkup:
    per_page = 5
    total    = len(courses_df)
    start    = page * per_page
    end      = min(start + per_page, total)
    buttons  = [
        [InlineKeyboardButton(str(r['course_name']), callback_data=f"adm_au_c_{int(r['id'])}")]
        for _, r in courses_df.iloc[start:end].iterrows()
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"adm_au_pg_{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"adm_au_pg_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("Back", callback_data="adm_back")])
    return InlineKeyboardMarkup(buttons)


def _course_keyboard(prefix: str) -> InlineKeyboardMarkup:
    courses = load_courses()
    buttons = [
        [InlineKeyboardButton(str(r['course_name']), callback_data=f"{prefix}{int(r['id'])}")]
        for _, r in courses.iterrows()
    ]
    # "new" suffix is the same for both prefixes (adm_course_ and adm_sg_)
    buttons.append([InlineKeyboardButton("➕ Add New Course", callback_data=f"{prefix}new")])
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────────────────────────
#  /startadmin
# ─────────────────────────────────────────────

async def startadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    await update.message.reply_text(
        "<b>Admin Panel</b>\n\nChoose an action:",
        reply_markup=_main_keyboard(),
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
#  Callback handler  (pattern: ^adm_)
# ─────────────────────────────────────────────

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    # ── Back to main menu ──
    if data == "adm_back":
        _state.pop(uid, None)
        await query.message.edit_text(
            "<b>Admin Panel</b>\n\nChoose an action:",
            reply_markup=_main_keyboard(),
            parse_mode="HTML"
        )
        return

    # ── Set Admin Group ──
    if data == "adm_setadmin":
        current = get_current_admin_group()
        if current:
            gname = html.escape(current['group_name'] or str(current['group_id']))
            await query.message.edit_text(
                f"<b>Admin Group Already Set</b>\n\n"
                f"Group: <b>{gname}</b>\n"
                f"ID: <code>{current['group_id']}</code>\n\n"
                "To change it, first remove the current admin group.",
                reply_markup=_admin_group_set_keyboard(),
                parse_mode="HTML"
            )
        else:
            await query.message.edit_text(
                "<b>Set Admin Group</b>\n\n"
                "Choose how to set the admin group:",
                reply_markup=_two_option_keyboard("adm_admin_link", "adm_admin_manual"),
                parse_mode="HTML"
            )

    elif data == "adm_removeadmin":
        removed = remove_admin_group()
        if removed:
            await query.message.edit_text(
                "<b>Admin Group Removed</b>\n\n"
                "Admin commands will no longer work in that group.\n"
                "Use <b>Set Admin Group</b> to configure a new one.",
                reply_markup=_main_keyboard(),
                parse_mode="HTML"
            )
        else:
            await query.message.edit_text(
                "No admin group was set.",
                reply_markup=_main_keyboard(),
                parse_mode="HTML"
            )

    elif data == "adm_admin_link":
        link = _bot_link("setadmin")
        await query.message.reply_text(
            "<b>Add Bot to Admin Group</b>\n\n"
            "Click the link below, select your admin group, and add the bot.\n"
            "The group will be set as admin group automatically.\n\n"
            f"<a href=\"{link}\">Tap to add bot to group</a>",
            parse_mode="HTML", disable_web_page_preview=True
        )

    elif data == "adm_admin_manual":
        _state[uid] = {'step': 'set_admin_id'}
        await query.message.reply_text(
            "Send the admin group ID (e.g. <code>-1001234567890</code>):",
            parse_mode="HTML"
        )

    # ── Add Sub Group ──
    elif data == "adm_addgroup":
        await query.message.edit_text(
            "<b>Add Sub Group</b>\n\n"
            "Choose how to add the group:",
            reply_markup=_two_option_keyboard("adm_subgroup_link", "adm_subgroup_manual"),
            parse_mode="HTML"
        )

    elif data == "adm_subgroup_link":
        link = _bot_link("setsubgroup")
        await query.message.reply_text(
            "<b>Add Bot to Sub Group</b>\n\n"
            "Click the link below, select the group, and add the bot.\n"
            "You will then be asked to pick a course for that group.\n\n"
            f"<a href=\"{link}\">Tap to add bot to group</a>",
            parse_mode="HTML", disable_web_page_preview=True
        )
        # Remember which admin triggered this so we can DM them for course selection
        _pending_subgroup[uid] = 0   # 0 = waiting for group to call back

    elif data == "adm_subgroup_manual":
        _state[uid] = {'step': 'addgroup_id'}
        await query.message.reply_text(
            "Send the sub group / channel ID (e.g. <code>-1001234567890</code>):",
            parse_mode="HTML"
        )

    # ── Course selection for manual sub-group flow (from admin group) ──
    elif data.startswith("adm_course_"):
        suffix = data.replace("adm_course_", "")
        if suffix == "new":
            # Remember context so we can return to group linking after course is saved
            group_id = _state.get(uid, {}).get('group_id')
            _state[uid] = {'step': 'addcourse_for_group', 'group_id': group_id, 'prefix': 'adm_course_'}
            await query.message.reply_text("Send the new course name:")
            return
        course_id = int(suffix)
        if uid not in _state or 'group_id' not in _state.get(uid, {}):
            await query.message.reply_text("Session expired. Use /startadmin again.")
            return
        await _finish_group_save(uid, course_id, query.message)

    # ── Course selection for via-link sub-group flow (from within the sub-group) ──
    elif data.startswith("adm_sg_"):
        suffix = data.replace("adm_sg_", "")
        group_chat_id = query.message.chat_id
        if suffix == "new":
            _state[uid] = {'step': 'addcourse_for_group', 'group_id': group_chat_id, 'prefix': 'adm_sg_', 'in_group': True}
            await query.message.reply_text("Send the new course name:")
            return
        course_id = int(suffix)
        courses = load_courses()
        row     = courses[courses['id'] == course_id]
        if row.empty:
            await query.message.reply_text("Course not found.")
            return
        course_name = str(row.iloc[0]['course_name'])
        save_group(group_chat_id, course_name, group_name=query.message.chat.title or '')
        await query.message.edit_text(
            f"Group linked to course <b>{html.escape(course_name)}</b>",
            parse_mode="HTML"
        )

    # ── Other buttons ──
    elif data == "adm_addcourse":
        _state[uid] = {'step': 'addcourse'}
        await query.message.reply_text("Send the new course name:")

    elif data == "adm_delcourse":
        await query.message.edit_text(
            "<b>Delete Course</b>\n\nTap 🗑 next to the course to delete it:",
            reply_markup=_delete_course_keyboard(page=0),
            parse_mode="HTML"
        )

    elif data.startswith("adm_dc_pg_"):
        page = int(data.replace("adm_dc_pg_", ""))
        await query.message.edit_text(
            "<b>Delete Course</b>\n\nTap 🗑 next to the course to delete it:",
            reply_markup=_delete_course_keyboard(page=page),
            parse_mode="HTML"
        )

    elif data.startswith("adm_dc_n_"):
        pass  # course name button — no action

    elif data.startswith("adm_dc_"):
        # format: adm_dc_{course_id}_{page}
        parts     = data.replace("adm_dc_", "").split("_")
        course_id = int(parts[0])
        page      = int(parts[1]) if len(parts) > 1 else 0
        delete_course(course_id)
        courses = load_courses()
        if courses.empty:
            await query.message.edit_text(
                "<b>Delete Course</b>\n\nNo courses left.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="adm_back")]]),
                parse_mode="HTML"
            )
        else:
            # Stay on same page, but clamp if last item on page was deleted
            max_page = max(0, (len(courses) - 1) // 5)
            page     = min(page, max_page)
            await query.message.edit_text(
                "<b>Delete Course</b>\n\nCourse deleted. Tap 🗑 to delete another:",
                reply_markup=_delete_course_keyboard(page=page),
                parse_mode="HTML"
            )

    elif data == "adm_adduser":
        courses = load_courses()
        if courses.empty:
            await query.message.reply_text(
                "No courses configured. Add a course first via <b>Add Course</b>.",
                parse_mode="HTML"
            )
            return
        await query.message.edit_text(
            "<b>Add User</b>\n\nSelect the course:",
            reply_markup=_adduser_course_keyboard(courses, page=0),
            parse_mode="HTML"
        )

    elif data.startswith("adm_au_pg_"):
        page    = int(data.replace("adm_au_pg_", ""))
        courses = load_courses()
        await query.message.edit_text(
            "<b>Add User</b>\n\nSelect the course:",
            reply_markup=_adduser_course_keyboard(courses, page=page),
            parse_mode="HTML"
        )

    elif data.startswith("adm_au_c_"):
        course_id = int(data.replace("adm_au_c_", ""))
        courses   = load_courses()
        row       = courses[courses['id'] == course_id]
        if row.empty:
            await query.message.reply_text("Course not found.")
            return
        course_name = str(row.iloc[0]['course_name'])
        _state[uid] = {'step': 'adduser_period', 'course_name': course_name}
        period_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 Month",   callback_data="adm_au_p_1"),
             InlineKeyboardButton("2 Months",  callback_data="adm_au_p_2"),
             InlineKeyboardButton("3 Months",  callback_data="adm_au_p_3")],
            [InlineKeyboardButton("6 Months",  callback_data="adm_au_p_6"),
             InlineKeyboardButton("12 Months", callback_data="adm_au_p_12")],
            [InlineKeyboardButton("Back", callback_data="adm_back")],
        ])
        await query.message.edit_text(
            f"<b>Add User</b>\n\n"
            f"Course: <b>{html.escape(course_name)}</b>\n\n"
            f"Select subscription period:",
            reply_markup=period_kb,
            parse_mode="HTML"
        )

    elif data.startswith("adm_au_p_"):
        months = int(data.replace("adm_au_p_", ""))
        if uid not in _state or 'course_name' not in _state.get(uid, {}):
            await query.message.reply_text("Session expired. Use /startadmin again.")
            return
        course_name = _state.pop(uid)['course_name']

        from datetime import datetime, timedelta
        today    = datetime.now().date()
        end_date = today + timedelta(days=30 * months)

        group_id = get_group_for_course(course_name)
        if not group_id:
            await query.message.edit_text(
                f"No group mapped for course <b>{html.escape(course_name)}</b>.\n"
                "Map a group first via <b>Add Sub Group</b>.",
                reply_markup=_main_keyboard(),
                parse_mode="HTML"
            )
            return

        try:
            link_name = f"adm_{course_name[:8]}_{today}"
            invite    = await context.bot.create_chat_invite_link(
                chat_id=group_id, member_limit=1, name=link_name
            )
            invite_url = invite.invite_link
            save_pending_invite(invite_url, link_name, course_name, months,
                                str(today), str(end_date))
            await query.message.edit_text(
                f"<b>Invite Link Created!</b>\n\n"
                f"Course: <b>{html.escape(course_name)}</b>\n"
                f"Period: <b>{months} Month{'s' if months > 1 else ''}</b>\n"
                f"Start: <code>{today}</code>\n"
                f"End:   <code>{end_date}</code>\n\n"
                f"Share this link with the subscriber:\n"
                f"<code>{html.escape(invite_url)}</code>\n\n"
                f"Their Telegram ID will be saved automatically when they join.",
                parse_mode="HTML"
            )
        except Exception as e:
            await query.message.reply_text(
                f"Error creating invite link: {html.escape(str(e))}",
                parse_mode="HTML"
            )

    elif data == "adm_seeusers":
        _state[uid] = {'step': 'seeusers'}
        await query.message.reply_text("Send user ID or username to search:")

    elif data == "adm_allusers":
        await _send_all_users(query.message)


async def _finish_group_save(uid: int, course_id: int, message):
    courses = load_courses()
    row     = courses[courses['id'] == course_id]
    if row.empty:
        await message.reply_text("Course not found.")
        return
    course_name = str(row.iloc[0]['course_name'])
    group_id    = _state[uid]['group_id']
    save_group(group_id, course_name, group_name='')
    del _state[uid]
    await message.reply_text(
        f"Group <code>{group_id}</code> linked to course <b>{html.escape(course_name)}</b>",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
#  Message handler  (admin group text input)
# ─────────────────────────────────────────────

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != 'private' and chat.id != get_admin_group_id():
        return
    uid  = update.effective_user.id
    if uid not in _state:
        return

    step = _state[uid]['step']
    text = update.message.text.strip()

    if step == 'addcourse':
        save_course(text)
        del _state[uid]
        await update.message.reply_text(
            f"Course <b>{html.escape(text)}</b> saved",
            parse_mode="HTML"
        )

    elif step == 'addcourse_for_group':
        save_course(text)
        group_id = _state[uid].get('group_id')
        prefix   = _state[uid].get('prefix', 'adm_course_')
        del _state[uid]
        await update.message.reply_text(
            f"Course <b>{html.escape(text)}</b> saved.\n\nNow select the course for the group:",
            reply_markup=_course_keyboard(prefix),
            parse_mode="HTML"
        )
        if group_id:
            _state[uid] = {'step': 'addgroup_course', 'group_id': group_id}

    elif step == 'addgroup_id':
        try:
            group_id = int(text)
        except ValueError:
            await update.message.reply_text("Invalid ID — send a number like -1001234567890")
            return
        _state[uid] = {'step': 'addgroup_course', 'group_id': group_id}
        kb = _course_keyboard("adm_course_")
        await update.message.reply_text(
            f"Group <code>{group_id}</code> — select its course:",
            reply_markup=kb,
            parse_mode="HTML"
        )

    elif step == 'set_admin_id':
        try:
            gid = int(text)
        except ValueError:
            await update.message.reply_text("Invalid ID — send a number like -1001234567890")
            return
        set_admin_group_id(gid)
        del _state[uid]
        await update.message.reply_text(
            f"Admin group set to <code>{gid}</code>",
            parse_mode="HTML"
        )

    elif step == 'seeusers':
        del _state[uid]
        await _search_user(update.message, text)


# ─────────────────────────────────────────────
#  Group setup handler
#  Called when bot receives /start in a GROUP
#  (registered in bot.py for ChatType.GROUPS)
# ─────────────────────────────────────────────

async def handle_group_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return
    payload  = context.args[0]
    chat_id  = update.effective_chat.id
    chat_name = update.effective_chat.title or str(chat_id)

    if payload == "setadmin":
        set_admin_group_id(chat_id, group_name=chat_name)
        await update.message.reply_text(
            f"This group (<code>{chat_id}</code>) is now set as the <b>Admin Group</b>.",
            parse_mode="HTML"
        )

    elif payload == "setsubgroup":
        kb = _course_keyboard("adm_sg_")
        await update.message.reply_text(
            f"Bot added to <b>{html.escape(chat_name)}</b>\n\n"
            "Select the course this group belongs to:",
            reply_markup=kb,
            parse_mode="HTML"
        )


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

async def _send_all_users(message):
    try:
        if not os.path.exists(REGISTERED_USERS_FILE):
            await message.reply_text("No registered users yet.")
            return
        df = pd.read_excel(REGISTERED_USERS_FILE)
        if df.empty:
            await message.reply_text("No registered users yet.")
            return
        lines = []
        for _, r in df.iterrows():
            status = "removed" if str(r.get('isremoved', 0)) == "1" else "active"
            lines.append(
                f"ID:{r['userid']} | {r['username']} | {r['plan_type']} | "
                f"exp:{r['end_date']} | {status}"
            )
        text = "\n".join(lines)
        for chunk in [text[i:i+3800] for i in range(0, len(text), 3800)]:
            await message.reply_text(
                f"<pre>{html.escape(chunk)}</pre>", parse_mode="HTML"
            )
    except Exception as e:
        await message.reply_text(f"Error: {html.escape(str(e))}", parse_mode="HTML")


async def _search_user(message, query: str):
    try:
        if not os.path.exists(REGISTERED_USERS_FILE):
            await message.reply_text("No registered users yet.")
            return
        df      = pd.read_excel(REGISTERED_USERS_FILE)
        results = df[
            (df['userid'].astype(str) == query) |
            (df['username'].astype(str).str.lower() == query.lower())
        ]
        if results.empty:
            await message.reply_text("No user found.")
            return
        lines = []
        for _, r in results.iterrows():
            lines.append(
                f"ID: {r['userid']}\n"
                f"Username: {r['username']}\n"
                f"Plan: {r['plan_type']}\n"
                f"Expires: {r['end_date']}\n"
                f"Status: {'removed' if str(r.get('isremoved', 0)) == '1' else 'active'}\n"
            )
        await message.reply_text(
            f"<pre>{html.escape(chr(10).join(lines))}</pre>",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.reply_text(f"Error: {html.escape(str(e))}", parse_mode="HTML")


# ─────────────────────────────────────────────
#  Web Admin Management  (superadmin bot commands)
#  Only ADMIN_USER_ID can run these.
# ─────────────────────────────────────────────

def _is_superadmin(update: Update) -> bool:
    return (update.effective_chat.type == 'private' and
            update.effective_user.id == ADMIN_USER_ID)


async def webadmin_list_command(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """/webadmins — list all web panel admin accounts."""
    if not _is_superadmin(update):
        return
    try:
        admins = db_get_all_admin_users()
        if not admins:
            await update.message.reply_text("No admin accounts found.")
            return
        lines = ["#    Username             Role",
                 "─" * 36]
        for i, a in enumerate(admins, 1):
            lines.append(f"{i:<4} {a['username']:<20} {a['role']}")
        await update.message.reply_text(
            f"<pre>{html.escape(chr(10).join(lines))}</pre>",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {html.escape(str(e))}", parse_mode="HTML")


async def webadmin_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addwebadmin username password [role] — add a web panel admin."""
    if not _is_superadmin(update):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /addwebadmin &lt;username&gt; &lt;password&gt; [admin|superadmin]",
            parse_mode="HTML"
        )
        return
    username = context.args[0].strip()
    password = context.args[1]
    role     = context.args[2] if len(context.args) >= 3 else "admin"
    if role not in ("admin", "superadmin"):
        await update.message.reply_text("Role must be <code>admin</code> or <code>superadmin</code>.", parse_mode="HTML")
        return
    if len(password) < 6:
        await update.message.reply_text("Password must be at least 6 characters.")
        return
    try:
        pw_hash = _hash_password(password)
        db_create_admin_user(username, pw_hash, role=role)
        await update.message.reply_text(
            f"✅ Admin <b>{html.escape(username)}</b> (<code>{role}</code>) added.",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {html.escape(str(e))}", parse_mode="HTML")


async def webadmin_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/removewebadmin username — remove a web panel admin (cannot remove superadmin)."""
    if not _is_superadmin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removewebadmin &lt;username&gt;", parse_mode="HTML")
        return
    username = context.args[0].strip()
    try:
        removed = db_remove_admin_user(username)
        if removed:
            await update.message.reply_text(f"✅ Admin <b>{html.escape(username)}</b> removed.", parse_mode="HTML")
        else:
            await update.message.reply_text(
                f"Not found or <b>{html.escape(username)}</b> is a superadmin (cannot remove).",
                parse_mode="HTML"
            )
    except Exception as e:
        await update.message.reply_text(f"Error: {html.escape(str(e))}", parse_mode="HTML")


async def webadmin_changepass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/changewebpass username newpassword — change any admin's password."""
    if not _is_superadmin(update):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /changewebpass &lt;username&gt; &lt;newpassword&gt;", parse_mode="HTML")
        return
    username = context.args[0].strip()
    password = context.args[1]
    if len(password) < 6:
        await update.message.reply_text("Password must be at least 6 characters.")
        return
    try:
        pw_hash = _hash_password(password)
        db_change_admin_password(username, pw_hash)
        await update.message.reply_text(
            f"✅ Password changed for <b>{html.escape(username)}</b>.",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {html.escape(str(e))}", parse_mode="HTML")
