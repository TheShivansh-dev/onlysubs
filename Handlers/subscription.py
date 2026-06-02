from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from .config import (
    get_admin_group_id,
    ADMIN_USER_ID,
    BOT_CREATOR_USER_ID,
    get_pending_invite,
    mark_pending_invite_used,
    SUPPORT_CONTACT,
    REGISTERED_USERS_FILE,
    INVITE_LINKS_FILE,
    PAID_USERS_FILE,
    USER_JOIN_LOGS_FILE,
    SUBSCRIPTION_PLANS,
    CHANNEL_MAPPING,
    get_months,
    get_course_info,
    get_display_message,
    get_plan_display_label,
    init_course_names_file,
    get_group_for_course,
    # GUID
    parse_start_param,
    verify_guid,
    mark_guid_used,
    generate_guids,
    build_start_link,
    GUID_OK,
    GUID_USED,
    GUID_NOT_FOUND,
)
from .db import (
    db_save_invite_link,
    db_save_registered_user,
    db_get_active_registered_users,
    db_remove_registered_user,
    db_get_user_by_userid,
    db_add_paid_user,
    db_remove_paid_user,
    db_edit_paid_user,
    db_get_paid_user,
    db_get_all_paid_users,
    db_get_all_registered_users,
    db_generate_guids,
    db_get_guid_samples,
)
import os
import html
from datetime import datetime, timedelta, date
import uuid


def log(msg):
    """Safe print that handles Windows cp1252 encoding."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', errors='replace').decode('ascii'))


# ─────────────────────────────────────────────
#  INVITE LINK
# ─────────────────────────────────────────────

async def create_invite_link(context: ContextTypes.DEFAULT_TYPE,
                             user_id: int, channel_id: int) -> str | None:
    try:
        link_id = str(uuid.uuid4())[:8]
        log(f"\n[LINK_CREATE] user={user_id}  channel={channel_id}")

        invite_link = await context.bot.create_chat_invite_link(
            chat_id=channel_id,
            member_limit=1,
            name=f"user_{user_id}_{link_id}"
        )
        log(f"[LINK_CREATE] created: {invite_link.invite_link}")

        db_save_invite_link(link_id, user_id, invite_link.invite_link)

        return invite_link.invite_link

    except BadRequest as e:
        log(f"[LINK_CREATE] BadRequest: {e}  channel={channel_id}  user={user_id}")
        return None
    except Exception as e:
        log(f"[LINK_CREATE] Error: {e}  channel={channel_id}  user={user_id}")
        return None


# ─────────────────────────────────────────────
#  KEYBOARD
# ─────────────────────────────────────────────

def get_subscription_keyboard() -> InlineKeyboardMarkup:
    init_course_names_file()
    keyboard = [
        [InlineKeyboardButton(get_plan_display_label(key), callback_data=f"sub_{key}")]
        for key in SUBSCRIPTION_PLANS
    ]
    return InlineKeyboardMarkup(keyboard)


# ─────────────────────────────────────────────
#  REGISTER USER
# ─────────────────────────────────────────────

async def register_user_subscription(user_id, username, plan_key,
                                     context: ContextTypes.DEFAULT_TYPE):
    plan       = SUBSCRIPTION_PLANS[plan_key]
    months     = get_months(plan['month_code'])
    today      = datetime.now().date()
    end_date   = today + timedelta(days=30 * months)

    course_name = get_course_info(plan_key)['course_name']
    channel_id  = get_group_for_course(course_name) or CHANNEL_MAPPING.get(plan_key, -1234567890)
    invite_link = await create_invite_link(context, user_id, channel_id)
    link_id     = str(uuid.uuid4())[:8]

    if not invite_link:
        raise Exception("Failed to create invite link")

    db_save_registered_user(
        userid=user_id,
        username=username,
        invite_link_id=link_id,
        invite_link_url=invite_link,
        registration_date=str(today),
        end_date=str(end_date),
        plan_type=plan_key,
        link_used=0,
    )

    course_info  = get_course_info(plan_key)
    plan_display = get_plan_display_label(plan_key)
    return invite_link, end_date, plan_display, course_info


# ─────────────────────────────────────────────
#  CALLBACKS
# ─────────────────────────────────────────────

async def handle_subscription_callback(update: Update,
                                       context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    plan_key = query.data.replace('sub_', '')
    user_id  = query.from_user.id
    username = query.from_user.username or query.from_user.first_name

    if plan_key not in SUBSCRIPTION_PLANS:
        await query.edit_message_text("Invalid plan selected.")
        return

    try:
        invite_link, end_date, plan_display, _ = \
            await register_user_subscription(user_id, username, plan_key, context)

        await query.edit_message_text(
            f"<b>Subscription Confirmed!</b>\n\n"
            f"Plan: {plan_display}\n"
            f"Registered: {datetime.now().strftime('%Y-%m-%d')}\n"
            f"Expires: {end_date}\n\n"
            f"Your exclusive access is ready!",
            parse_mode="HTML"
        )

        msg  = get_display_message(plan_key, end_date)
        kbrd = [[InlineKeyboardButton("Join Here", url=invite_link)]]
        await context.bot.send_message(
            chat_id=user_id, text=msg,
            reply_markup=InlineKeyboardMarkup(kbrd), parse_mode="HTML"
        )

    except Exception as e:
        log(f"Error registering subscription: {e}")
        await query.edit_message_text(
            f"Error: {html.escape(str(e))}", parse_mode="HTML"
        )


# ─────────────────────────────────────────────
#  DAILY EXPIRY CHECK  (called by job_queue at 8 AM)
# ─────────────────────────────────────────────

async def _send_payment_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Send monthly payment reminder on the 1st of each month."""
    logo_path = "Assets/Logo.jpg"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Approve Payment", callback_data="pay_approve")]])

    try:
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as f:
                await context.bot.send_photo(
                    chat_id=ADMIN_USER_ID,
                    photo=f,
                    caption=(
                        "<b>Monthly Service Payment</b>\n\n"
                        "Today is the 1st. Please complete the monthly payment "
                        "to keep the bot running."
                    ),
                    reply_markup=kb,
                    parse_mode="HTML"
                )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=(
                    "<b>Monthly Service Payment</b>\n\n"
                    "Today is the 1st. Please complete the monthly payment "
                    "to keep the bot running."
                ),
                reply_markup=kb,
                parse_mode="HTML"
            )
    except Exception as e:
        log(f"[PAYMENT] Error sending to admin: {e}")

    try:
        await context.bot.send_message(
            chat_id=BOT_CREATOR_USER_ID,
            text=(
                "<b>Payment Reminder</b>\n\n"
                "Today is the 1st — monthly payment is expected today.\n"
                "You will be notified once the admin approves it."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        log(f"[PAYMENT] Error sending reminder to creator: {e}")


async def handle_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Payment marked as approved.")

    try:
        approver = query.from_user.username or query.from_user.first_name
        await context.bot.send_message(
            chat_id=BOT_CREATOR_USER_ID,
            text=(
                "<b>Payment Done</b>\n\n"
                f"Admin (<b>{html.escape(approver)}</b>) has approved the payment.\n"
                "Please verify and confirm receipt."
            ),
            parse_mode="HTML"
        )
        try:
            await query.edit_message_caption(
                "<b>Payment Approved</b>\n\nThe creator has been notified to verify.",
                parse_mode="HTML"
            )
        except Exception:
            await query.edit_message_text(
                "<b>Payment Approved</b>\n\nThe creator has been notified to verify.",
                parse_mode="HTML"
            )
    except Exception as e:
        log(f"[PAYMENT] Error notifying creator on approve: {e}")


async def check_subscription_expiry(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().date()
    if today.day == 1:
        await _send_payment_reminder(context)

    try:
        active_users = db_get_active_registered_users()
    except Exception as e:
        log(f"[EXPIRY] Could not load users: {e}")
        return

    for row in active_users:
        user_id = row['userid']
        try:
            end_date_val = row['end_date']
            if isinstance(end_date_val, str):
                end_date = datetime.strptime(end_date_val, '%Y-%m-%d').date()
            else:
                end_date = end_date_val  # already a date object from psycopg2

            days_remaining = (end_date - today).days

            if days_remaining == 5:
                await context.bot.send_message(
                    chat_id=user_id, parse_mode="HTML",
                    text=(
                        "<b>Subscription Expiring Soon!</b>\n\n"
                        "Your subscription expires in <b>5 days</b>.\n"
                        "Renew now to keep access.\n\n"
                        "Click /start to renew."
                    )
                )

            elif days_remaining == 3:
                await context.bot.send_message(
                    chat_id=user_id, parse_mode="HTML",
                    text=(
                        "<b>Final Reminder!</b>\n\n"
                        "Your subscription expires in <b>3 days</b>.\n"
                        "This is your last chance to renew.\n\n"
                        "Click /start to renew now."
                    )
                )

            elif days_remaining <= 0:
                await context.bot.send_message(
                    chat_id=user_id, parse_mode="HTML",
                    text=(
                        "<b>Subscription Expired</b>\n\n"
                        "You have been removed from the group.\n"
                        "To regain access, purchase a new plan.\n\n"
                        "Click /start to purchase."
                    )
                )

                _plan_type = row.get('plan_type', 'plan4')
                _cname     = get_course_info(str(_plan_type)).get('course_name', '')
                channel_id = get_group_for_course(_cname) or CHANNEL_MAPPING.get(str(_plan_type), -1234567890)
                try:
                    await context.bot.ban_chat_member(
                        chat_id=channel_id, user_id=user_id
                    )
                except Exception as e:
                    log(f"Could not ban user {user_id}: {e}")

                db_remove_registered_user(user_id, str(today))

        except Exception as e:
            log(f"Error processing user {user_id}: {e}")


# ─────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    # ── GUID-based deep link: ?start={guid}_{planKey}_{monthCode} ──
    if context.args:
        parsed = parse_start_param(context.args[0])

        if parsed is None:
            await update.message.reply_text(
                "Invalid or expired link.\n\n"
                f"If you think this is a mistake, contact {SUPPORT_CONTACT}",
                parse_mode="HTML"
            )
            return

        guid, plan_key, _ = parsed
        status = verify_guid(guid)

        if status == GUID_NOT_FOUND:
            await update.message.reply_text(
                "<b>Invalid Link</b>\n\n"
                "This access link does not exist or has been tampered with.\n\n"
                f"Contact {SUPPORT_CONTACT} for help.",
                parse_mode="HTML"
            )
            return

        if status == GUID_USED:
            # Check if THIS user is already in registered_users with active subscription
            user_row = db_get_user_by_userid(user_id)

            if user_row and int(user_row.get('isremoved', 1)) == 0:
                today = datetime.now().date()
                try:
                    end_date_val = user_row['end_date']
                    if isinstance(end_date_val, str):
                        exp = datetime.strptime(end_date_val, '%Y-%m-%d').date()
                    else:
                        exp = end_date_val
                except Exception:
                    exp = today  # fallback: treat as active

                if exp >= today:
                    # Same user, active subscription — send a fresh invite link
                    _cname2 = get_course_info(str(user_row['plan_type'])).get('course_name', '')
                    ch_id   = get_group_for_course(_cname2) or CHANNEL_MAPPING.get(str(user_row['plan_type']), -1234567890)
                    invite_link = await create_invite_link(context, user_id, ch_id)
                    if invite_link:
                        kbrd = [[InlineKeyboardButton("Join Here", url=invite_link)]]
                        await update.message.reply_text(
                            "<b>Already Registered</b>\n\n"
                            "You are already subscribed. Here is a fresh invite link:",
                            reply_markup=InlineKeyboardMarkup(kbrd),
                            parse_mode="HTML"
                        )
                    else:
                        await update.message.reply_text(
                            f"Could not create invite link. Contact {SUPPORT_CONTACT}",
                            parse_mode="HTML"
                        )
                else:
                    await update.message.reply_text(
                        f"<b>Subscription Expired</b>\n\n"
                        f"Your subscription expired on {user_row['end_date']}.\n"
                        f"Contact {SUPPORT_CONTACT} to renew.",
                        parse_mode="HTML"
                    )
            else:
                # Different user trying to reuse this GUID — block
                await update.message.reply_text(
                    "<b>Access Not Allowed</b>\n\n"
                    "This link has already been used by another account.\n\n"
                    f"If you think this is a mistake, contact {SUPPORT_CONTACT}",
                    parse_mode="HTML"
                )
            return

        # status == GUID_OK — register the user
        try:
            invite_link, end_date, plan_display, _ = \
                await register_user_subscription(user_id, username, plan_key, context)

            mark_guid_used(guid)

            await update.message.reply_text(
                f"<b>Subscription Activated!</b>\n\n"
                f"Plan: {plan_display}\n"
                f"Expires: {end_date}\n\n"
                f"Your exclusive access is ready!",
                parse_mode="HTML"
            )

            msg  = get_display_message(plan_key, end_date)
            kbrd = [[InlineKeyboardButton("Join Here", url=invite_link)]]
            await context.bot.send_message(
                chat_id=user_id, text=msg,
                reply_markup=InlineKeyboardMarkup(kbrd), parse_mode="HTML"
            )

        except Exception as e:
            log(f"Error registering from link: {e}")
            await update.message.reply_text(
                f"Error: {html.escape(str(e))}\n\nPlease try again or contact {SUPPORT_CONTACT}",
                parse_mode="HTML"
            )
        return

    # ── No args: show plan selection keyboard ──
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "<b>Welcome to OnlySubscriber!</b>\n\n"
            "Access exclusive courses and learning materials.\n\n"
            "Choose a subscription plan to get started:"
        ),
        reply_markup=get_subscription_keyboard(),
        parse_mode="HTML"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id, parse_mode="HTML",
        text=(
            "<b>OnlySubscriber Help</b>\n\n"
            "/start - View subscription plans\n"
            "/getuserid - Get your User ID\n"
            "/help - Show this message\n\n"
            "How it works:\n"
            "1. Click /start to see plans\n"
            "2. Select your subscription\n"
            "3. Get instant group access\n"
            "4. Receive expiry reminders\n\n"
            "Need help? Contact: @support"
        )
    )


async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        all_users = db_get_all_registered_users()
    except Exception:
        await update.message.reply_text("No data available.")
        return

    if not all_users:
        await update.message.reply_text("No registered users yet.")
        return

    today   = datetime.now().date()
    active  = [u for u in all_users if int(u.get('isremoved', 0)) == 0]
    removed = [u for u in all_users if int(u.get('isremoved', 0)) == 1]

    expiring = 0
    for u in active:
        try:
            end_val = u['end_date']
            if isinstance(end_val, str):
                ed = datetime.strptime(end_val, '%Y-%m-%d').date()
            else:
                ed = end_val
            days = (ed - today).days
            if 0 <= days <= 5:
                expiring += 1
        except Exception:
            pass

    plan_counts: dict = {}
    for u in active:
        pt = str(u.get('plan_type', 'unknown'))
        plan_counts[pt] = plan_counts.get(pt, 0) + 1

    text = (
        f"<b>Subscription Statistics</b>\n\n"
        f"Total: {len(all_users)}\n"
        f"Active: {len(active)}\n"
        f"Removed: {len(removed)}\n"
        f"Expiring (5 days): {expiring}\n\n"
        f"<b>By Plan:</b>\n"
    )
    for plan, count in plan_counts.items():
        text += f"  {plan}: {count}\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def get_user_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    await context.bot.send_message(
        chat_id=update.effective_chat.id, parse_mode="HTML",
        text=(
            f"<b>Your User Information</b>\n\n"
            f"User ID: <code>{user_id}</code>\n"
            f"Username: @{username}"
        )
    )


async def get_chat_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id    = update.effective_chat.id
    chat_title = update.effective_chat.title or "Direct Message"
    chat_type  = update.effective_chat.type
    log(f"\nChat: {chat_title}  ID: {chat_id}  Type: {chat_type}\n")
    await context.bot.send_message(
        chat_id=chat_id, parse_mode="HTML",
        text=(
            f"<b>Chat Information</b>\n\n"
            f"Name: {html.escape(chat_title)}\n"
            f"ID: <code>{chat_id}</code>\n"
            f"Type: {chat_type}"
        )
    )


async def log_user_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log new chat members to console (file write removed)."""
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        user_id  = member.id
        username = member.username or member.first_name
        chat_id  = update.effective_chat.id
        log(f"[JOIN] {username} (ID: {user_id}) joined chat {chat_id}")


# ─────────────────────────────────────────────
#  ADMIN COMMANDS  (admin group only)
# ─────────────────────────────────────────────

def _admin_only(update: Update) -> bool:
    return update.effective_chat.id == get_admin_group_id()


async def add_paid_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin_only(update):
        await update.message.reply_text("Admin group only.")
        return
    if not context.args or len(context.args) < 4:
        await update.message.reply_text(
            "<b>Add Paid User</b>\n\n"
            "Usage: /adduser USER_ID COURSE START_DATE END_DATE\n"
            "Example: /adduser 123456789 stemTreky 2026-05-16 2026-08-16\n\n"
            "Courses: " + ", ".join(SUBSCRIPTION_PLANS.keys()),
            parse_mode="HTML"
        )
        return
    try:
        user_id    = int(context.args[0])
        course     = context.args[1]
        start_date = context.args[2]
        end_date   = context.args[3]

        if course not in SUBSCRIPTION_PLANS:
            await update.message.reply_text(f"Invalid course: {course}")
            return
        datetime.strptime(start_date, '%Y-%m-%d')
        datetime.strptime(end_date,   '%Y-%m-%d')

        db_add_paid_user(user_id, f"user_{user_id}", course, start_date, end_date)

        await update.message.reply_text(
            f"<b>Paid User Added!</b>\n\n"
            f"User ID: {user_id}\n"
            f"Course: {get_plan_display_label(course)}\n"
            f"Start: {start_date}\n"
            f"End: {end_date}",
            parse_mode="HTML"
        )
    except ValueError as e:
        await update.message.reply_text(f"Invalid input: {html.escape(str(e))}")


async def list_paid_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin_only(update):
        await update.message.reply_text("Admin group only.")
        return
    users = db_get_all_paid_users()
    active = [u for u in users if int(u.get('is_active', 0)) == 1]
    if not active:
        await update.message.reply_text("No paid users.")
        return
    text = "<b>Paid Users</b>\n\n<code>"
    for i, u in enumerate(active, 1):
        text += (
            f"#{i} | ID: {u['user_id']}\n"
            f"  Course: {u['course']}\n"
            f"  {u['start_date']} -> {u['end_date']}\n\n"
        )
    text += "</code>"
    await update.message.reply_text(text, parse_mode="HTML")


async def remove_paid_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin_only(update):
        await update.message.reply_text("Admin group only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeuser USER_ID")
        return
    try:
        user_id = int(context.args[0])
        existing = db_get_paid_user(user_id)
        if not existing:
            await update.message.reply_text(f"User {user_id} not found.")
            return
        db_remove_paid_user(user_id)
        await update.message.reply_text(f"User {user_id} removed.")
    except ValueError:
        await update.message.reply_text("Invalid User ID.")


async def view_all_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin_only(update):
        await update.message.reply_text("Admin group only.")
        return

    paid_users = db_get_all_paid_users()
    reg_users  = db_get_all_registered_users()

    text = "<b>ALL USERS</b>\n\n"
    active_paid = [u for u in paid_users if int(u.get('is_active', 0)) == 1]
    if active_paid:
        text += "<b>-- PAID (Manual) --</b>\n<code>"
        for u in active_paid:
            text += f"ID:{u['user_id']} | {u['course']} | {u['start_date']} -> {u['end_date']}\n"
        text += "</code>\n"

    active_reg = [u for u in reg_users if int(u.get('isremoved', 0)) == 0]
    if active_reg:
        text += "<b>-- REGISTERED (Auto) --</b>\n<code>"
        for u in active_reg:
            text += f"ID:{u['userid']} | {u['username']} | {u['plan_type']} | -> {u['end_date']}\n"
        text += "</code>\n"

    text += f"\nTotal Paid: {len(active_paid)} | Registered: {len(active_reg)} | Sum: {len(active_paid) + len(active_reg)}"
    await update.message.reply_text(text, parse_mode="HTML")


async def edit_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin_only(update):
        await update.message.reply_text("Admin group only.")
        return
    if not context.args or len(context.args) < 4:
        await update.message.reply_text(
            "<b>Edit User</b>\n\nUsage: /edituser USER_ID COURSE NEW_START NEW_END",
            parse_mode="HTML"
        )
        return
    try:
        user_id   = int(context.args[0])
        course    = context.args[1]
        new_start = context.args[2]
        new_end   = context.args[3]
        if course not in SUBSCRIPTION_PLANS:
            await update.message.reply_text(f"Invalid course: {course}")
            return
        datetime.strptime(new_start, '%Y-%m-%d')
        datetime.strptime(new_end,   '%Y-%m-%d')
        existing = db_get_paid_user(user_id)
        if not existing:
            await update.message.reply_text(f"User {user_id} not found.")
            return
        db_edit_paid_user(user_id, course, new_start, new_end)
        await update.message.reply_text(
            f"<b>User Updated</b>\n\nID: {user_id}\nCourse: {get_plan_display_label(course)}\n"
            f"Start: {new_start}\nEnd: {new_end}",
            parse_mode="HTML"
        )
    except ValueError as e:
        await update.message.reply_text(f"Invalid input: {html.escape(str(e))}")


async def gen_guid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /genguid PLAN_KEY MONTH_CODE [COUNT]
    Example: /genguid stemTreky xm4tw 100
    """
    if not _admin_only(update):
        await update.message.reply_text("Admin group only.")
        return

    if not context.args or len(context.args) < 2:
        plans_list = "\n".join(
            f"  {k}  ({v['month_code']})" for k, v in SUBSCRIPTION_PLANS.items()
        )
        await update.message.reply_text(
            "<b>Generate GUIDs</b>\n\n"
            "Usage: /genguid PLAN_KEY MONTH_CODE [COUNT]\n\n"
            "Example:\n"
            "<code>/genguid stemTreky xm4tw 100</code>\n\n"
            "<b>Available plans (plan_key  month_code):</b>\n"
            f"<code>{plans_list}</code>",
            parse_mode="HTML"
        )
        return

    plan_key   = context.args[0]
    month_code = context.args[1]
    count      = int(context.args[2]) if len(context.args) >= 3 else 100

    if plan_key not in SUBSCRIPTION_PLANS:
        await update.message.reply_text(f"Unknown plan: {plan_key}")
        return

    try:
        added   = generate_guids(plan_key, month_code, count)
        samples = db_get_guid_samples(3)

        sample_links = "\n".join(
            build_start_link(g, plan_key, month_code) for g in samples
        )

        await update.message.reply_text(
            f"<b>GUIDs Generated</b>\n\n"
            f"Plan: {get_plan_display_label(plan_key)}\n"
            f"Month Code: <code>{month_code}</code>\n"
            f"Count: {added}\n\n"
            f"<b>Sample links:</b>\n<code>{sample_links}</code>\n\n"
            f"GUIDs saved to database.",
            parse_mode="HTML"
        )

    except Exception as e:
        await update.message.reply_text(f"Error: {html.escape(str(e))}")


async def user_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin_only(update):
        await update.message.reply_text("Admin group only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /userinfo USER_ID")
        return
    try:
        user_id  = int(context.args[0])
        paid_row = db_get_paid_user(user_id)
        reg_row  = db_get_user_by_userid(user_id)

        text = f"<b>User ID: {user_id}</b>\n\n"
        if paid_row:
            text += (
                f"<b>PAID USER</b>\n"
                f"Course: {paid_row['course']}\n"
                f"Start: {paid_row['start_date']}  End: {paid_row['end_date']}\n"
                f"Status: {'Active' if int(paid_row['is_active']) == 1 else 'Inactive'}\n\n"
            )
        if reg_row:
            text += (
                f"<b>REGISTERED USER</b>\n"
                f"Username: {reg_row['username']}\n"
                f"Plan: {reg_row['plan_type']}\n"
                f"Registered: {reg_row.get('registration_date', 'N/A')}  Expires: {reg_row['end_date']}\n"
                f"Status: {'Active' if int(reg_row['isremoved']) == 0 else 'Removed'}\n"
            )
        if not paid_row and not reg_row:
            text = f"User {user_id} not found."
        await update.message.reply_text(text, parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("Invalid User ID.")


# ─────────────────────────────────────────────
#  MEMBER JOIN TRACKING
# ─────────────────────────────────────────────

async def handle_member_join_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    member = update.chat_member
    if not member or not member.invite_link:
        return
    if member.new_chat_member.status not in ('member', 'creator', 'administrator'):
        return

    invite_url = member.invite_link.invite_link
    pending    = get_pending_invite(invite_url)
    if not pending:
        return

    user     = member.new_chat_member.user
    user_id  = user.id
    username = user.username or user.first_name

    link_id = str(uuid.uuid4())[:8]
    db_save_registered_user(
        userid=user_id,
        username=username,
        invite_link_id=link_id,
        invite_link_url=invite_url,
        registration_date=pending['start_date'],
        end_date=pending['end_date'],
        plan_type=pending['course_name'],
        link_used=1,
    )

    mark_pending_invite_used(invite_url)
    log(f"[JOIN_TRACKED] user={user_id} (@{username}) -> course={pending['course_name']} expires={pending['end_date']}")
