from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .config import (
    get_support_contact,
    get_admin_group_id,
    get_course_by_code,
    get_course_by_name,
    get_claimed_link,
    claim_link,
    get_display_message,
    parse_start_param,
)
from .db import (
    db_save_registered_user,
    db_save_invite_link,
    db_set_registered_invite_link,
    db_get_active_registered_users,
    db_get_user_by_userid,
    db_get_all_paid_users,
    db_get_paid_user,
    db_remove_registered_user,
    db_remove_paid_user,
)
import html
import uuid
from datetime import datetime, timedelta, date


def log(msg):
    """Safe print that handles Windows cp1252 encoding."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', errors='replace').decode('ascii'))


# ─────────────────────────────────────────────
#  /start  — external-website deep link
#  Format: <UniqueId>_<CourseCode>_<Months>
# ─────────────────────────────────────────────

async def _make_one_time_invite(context: ContextTypes.DEFAULT_TYPE,
                                course: dict, user_id: int) -> str | None:
    """
    Create a fresh single-use (member_limit=1) invite link for this user.
    Needs the group's numeric chat id (course['group_id']) and the bot to be
    an admin there. Falls back to a static group_link if no group_id is set.
    """
    group_id = course.get('group_id')
    if group_id:
        try:
            link_id = str(uuid.uuid4())[:8]
            invite  = await context.bot.create_chat_invite_link(
                chat_id=int(group_id),
                member_limit=1,
                name=f"u{user_id}_{link_id}",
            )
            try:
                db_save_invite_link(link_id, user_id, invite.invite_link)
            except Exception as e:
                log(f"[LINK] could not record invite link: {e}")
            return invite.invite_link
        except Exception as e:
            log(f"[LINK] create_chat_invite_link failed for group {group_id}, user {user_id}: {e}")
            return None
    # No numeric group id configured — fall back to a static link if present.
    return course.get('group_link') or None


async def _is_group_member(context: ContextTypes.DEFAULT_TYPE,
                           group_id, user_id: int) -> bool:
    """True if the user is already inside the course group."""
    if not group_id:
        return False
    try:
        m = await context.bot.get_chat_member(int(group_id), user_id)
        return m.status in ("member", "administrator", "creator", "owner")
    except Exception:
        return False


async def _get_or_create_invite(context: ContextTypes.DEFAULT_TYPE,
                                course: dict, user_id: int) -> str | None:
    """
    Return THIS user's invite link, reusing the one already issued for them.
    A new member_limit=1 link is minted only if none was stored yet — so
    re-clicking the deep link never produces extra links that could be shared.
    """
    row = db_get_user_by_userid(user_id)
    stored = (row.get('invite_link_url') or '').strip() if row else ''
    if stored:
        return stored
    invite = await _make_one_time_invite(context, course, user_id)
    if invite:
        try:
            db_set_registered_invite_link(user_id, invite)
        except Exception as e:
            log(f"[LINK] could not persist invite link: {e}")
    return invite


async def _send_access(context: ContextTypes.DEFAULT_TYPE, update: Update,
                       course: dict, months: int, end_date):
    """Send the user's single group invite link + support contact."""
    invite = await _get_or_create_invite(context, course, update.effective_user.id)
    msg    = get_display_message(course['course_name'], months, end_date)
    if invite:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Join Group", url=invite)]])
        await update.message.reply_text(msg, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(
            msg + f"\n\n⚠️ Couldn't create your invite link — contact {get_support_contact()}",
            parse_mode="HTML"
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    # No deep-link payload → simple greeting.
    if not context.args:
        await update.message.reply_text(
            "<b>Welcome!</b>\n\n"
            "Use the access link you received to activate your subscription.\n\n"
            f"Need help? Contact {get_support_contact()}",
            parse_mode="HTML"
        )
        return

    parsed = parse_start_param(context.args[0])
    if parsed is None:
        await update.message.reply_text(
            f"<b>Invalid link.</b>\n\nThis access link is malformed.\n"
            f"Contact {get_support_contact()} for help.",
            parse_mode="HTML"
        )
        return

    unique_id, course_code, months = parsed

    course = get_course_by_code(course_code)
    if not course:
        await update.message.reply_text(
            f"<b>Course not found.</b>\n\nThis link points to a course that no longer exists.\n"
            f"Contact {get_support_contact()} for help.",
            parse_mode="HTML"
        )
        return

    # ── Approach A: trust-on-first-use, bound to one Telegram user ──
    claimed = get_claimed_link(unique_id)

    if claimed and claimed.get('claimed_userid') is not None:
        if claimed['claimed_userid'] == user_id:
            # Same user, same link.
            # If they're already inside the group, don't hand out any link.
            if await _is_group_member(context, course.get('group_id'), user_id):
                await update.message.reply_text(
                    "<b>You already have access ✅</b>\n\n"
                    "You're already a member of this group.\n\n"
                    f"Trouble? Contact {get_support_contact()}",
                    parse_mode="HTML"
                )
                return
            # Not a member yet → give back the SAME link issued earlier
            # (never a fresh one — that's what allowed the leak).
            row = db_get_user_by_userid(user_id)
            end_date = row['end_date'] if row else _today_plus(months)
            await _send_access(context, update, course, months, end_date)
        else:
            # A different account is trying to reuse this link → block.
            await update.message.reply_text(
                "<b>Link expired.</b>\n\n"
                "This access link has already been used by another account.\n\n"
                f"Contact {get_support_contact()}",
                parse_mode="HTML"
            )
        return

    # ── First use → register the subscription ──
    today    = datetime.now().date()
    end_date = today + timedelta(days=30 * months)

    db_save_registered_user(
        userid=user_id,
        username=username,
        invite_link_id=str(uuid.uuid4())[:8],
        invite_link_url='',
        registration_date=str(today),
        end_date=str(end_date),
        plan_type=course['course_name'],
        link_used=1,
    )
    claim_link(unique_id, user_id)

    log(f"[REGISTER] user={user_id} (@{username}) -> {course['course_name']} "
        f"({months}m) expires={end_date}")
    await _send_access(context, update, course, months, end_date)


def _today_plus(months: int):
    return datetime.now().date() + timedelta(days=30 * months)


# ─────────────────────────────────────────────
#  PUBLIC COMMANDS
# ─────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>Help</b>\n\n"
        "/start - Activate the access link you were given\n"
        "/getuserid - Get your Telegram User ID\n\n"
        f"Need help? Contact {get_support_contact()}",
        parse_mode="HTML"
    )


async def get_user_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    await update.message.reply_text(
        f"<b>Your User Information</b>\n\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Username: @{html.escape(str(username))}",
        parse_mode="HTML"
    )


async def get_details_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /getdetails
      • private, no args      → your own Telegram ID
      • group,   no args      → this group's ID
      • any chat + @username  → that user's Telegram ID
      • reply + /getdetails   → the replied user's Telegram ID
    """
    chat = update.effective_chat

    # 1) Username given → resolve to that user's Telegram ID
    if context.args:
        uname = context.args[0].lstrip('@')
        try:
            target = await context.bot.get_chat(f"@{uname}")
            name   = getattr(target, 'full_name', None) or getattr(target, 'title', '') or ''
            await update.message.reply_text(
                f"<b>User Details</b>\n\n"
                f"Username: @{html.escape(uname)}\n"
                + (f"Name: {html.escape(name)}\n" if name else "")
                + f"Telegram ID: <code>{target.id}</code>",
                parse_mode="HTML"
            )
        except Exception:
            await update.message.reply_text(
                f"Couldn't find <code>@{html.escape(uname)}</code>.\n"
                "The user needs a public username (and to have interacted with the bot). "
                "Tip: reply to their message with /getdetails instead.",
                parse_mode="HTML"
            )
        return

    # 2) Reply to someone → that user's Telegram ID
    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        await update.message.reply_text(
            f"<b>User Details</b>\n\n"
            f"Name: {html.escape(u.full_name)}\n"
            f"Username: @{html.escape(u.username or '-')}\n"
            f"Telegram ID: <code>{u.id}</code>",
            parse_mode="HTML"
        )
        return

    # 3) No args: private → your own ID; group → group ID
    if chat.type == 'private':
        u = update.effective_user
        await update.message.reply_text(
            f"<b>Your Details</b>\n\n"
            f"Name: {html.escape(u.full_name)}\n"
            f"Username: @{html.escape(u.username or '-')}\n"
            f"Telegram ID: <code>{u.id}</code>",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"<b>Group Details</b>\n\n"
            f"Name: {html.escape(chat.title or '-')}\n"
            f"Group ID: <code>{chat.id}</code>",
            parse_mode="HTML"
        )


async def get_chat_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    title = chat.title or "Direct Message"
    log(f"\nChat: {title}  ID: {chat.id}  Type: {chat.type}\n")
    await update.message.reply_text(
        f"<b>Chat Information</b>\n\n"
        f"Name: {html.escape(title)}\n"
        f"ID: <code>{chat.id}</code>\n"
        f"Type: {chat.type}",
        parse_mode="HTML"
    )


async def log_user_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log new chat members to console."""
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        username = member.username or member.first_name
        log(f"[JOIN] {username} (ID: {member.id}) joined chat {update.effective_chat.id}")


# ─────────────────────────────────────────────
#  EXPIRY  — daily job
#  Instead of auto-kicking, post expiring users to the admin group
#  with Save / Remove buttons. The actual removal happens in admin.py
#  when an admin taps a button.
# ─────────────────────────────────────────────

def _parse_end_date(end_date_val) -> date | None:
    if isinstance(end_date_val, date):
        return end_date_val
    if isinstance(end_date_val, str):
        try:
            return datetime.strptime(end_date_val, '%Y-%m-%d').date()
        except ValueError:
            return None
    return None


async def _notify_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str):
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
    except Exception as e:
        log(f"[NOTIFY] Could not message user {user_id}: {e}")


def _renew_message(course_name: str, days: int) -> str:
    """Reminder sent to the user 5 and 3 days before removal."""
    return (
        f"<b>Subscription Expiring Soon!</b>\n\n"
        f"Your <b>{html.escape(str(course_name))}</b> access expires in <b>{days} days</b>.\n\n"
        f"Please renew the same course to keep your access.\n"
        f"To renew, contact {get_support_contact()}"
    )


async def _post_expiry_card(context, admin_group, kind: str, user_id: int,
                            name: str, course_name: str, end_date):
    """Send one admin-group card (1 day before removal) with Save / Remove buttons."""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Save",        callback_data=f"exp_save_{kind}_{user_id}"),
        InlineKeyboardButton("❌ Remove now",  callback_data=f"exp_rem_{kind}_{user_id}"),
    ]])
    try:
        await context.bot.send_message(
            chat_id=admin_group,
            text=(
                "⚠️ <b>Expiring Tomorrow</b>\n\n"
                f"User: <b>{html.escape(str(name))}</b>\n"
                f"ID: <code>{user_id}</code>\n"
                f"Course: {html.escape(str(course_name))}\n"
                f"Expires: {end_date}\n\n"
                "This user will be <b>removed automatically</b> when it expires.\n"
                "Tap <b>Save</b> to keep them (extends 30 days), or "
                "<b>Remove now</b> to take them out immediately."
            ),
            reply_markup=kb,
            parse_mode="HTML",
        )
    except Exception as e:
        log(f"[EXPIRY] Could not post card for {user_id}: {e}")


# ─────────────────────────────────────────────
#  REMOVAL  (shared by the daily job and the admin Remove button)
# ─────────────────────────────────────────────

def resolve_group_id(kind: str, user_id: int):
    """Find the numeric group id a user belongs to, via their course."""
    if kind == "r":
        row = db_get_user_by_userid(user_id)
        cname = row.get('plan_type') if row else None
    else:
        row = db_get_paid_user(user_id)
        cname = row.get('course') if row else None
    if not cname:
        return None
    course = get_course_by_name(str(cname))
    return course.get('group_id') if course else None


async def kick_user(context: ContextTypes.DEFAULT_TYPE, group_id, user_id: int) -> bool:
    """
    Kick a user from the group WITHOUT leaving them banned. Telegram's only
    removal API is ban_chat_member, so we remove then IMMEDIATELY unban (in a
    finally) — the user is never left banned and can rejoin on a future
    subscription.
    """
    if not group_id:
        log(f"[KICK] No group_id for user {user_id}; skipping removal")
        return False
    try:
        await context.bot.ban_chat_member(chat_id=int(group_id), user_id=user_id)
        log(f"[KICK] Removed user {user_id} from group {group_id}")
        return True
    except Exception as e:
        log(f"[KICK] Could not remove user {user_id} from {group_id}: {e}")
        return False
    finally:
        # Always lift the ban so the user is never left blocked.
        try:
            await context.bot.unban_chat_member(
                chat_id=int(group_id), user_id=user_id, only_if_banned=True)
        except Exception as e:
            log(f"[KICK] Could not unban user {user_id} from {group_id}: {e}")


async def kick_and_deactivate(context: ContextTypes.DEFAULT_TYPE, kind: str, user_id: int) -> bool:
    """Remove a user from their group and mark them inactive in the DB."""
    group_id = resolve_group_id(kind, user_id)
    kicked   = await kick_user(context, group_id, user_id)
    if kind == "r":
        db_remove_registered_user(user_id, str(datetime.now().date()))
    else:
        db_remove_paid_user(user_id)
    return kicked


async def check_subscription_expiry(context: ContextTypes.DEFAULT_TYPE):
    today       = datetime.now().date()
    admin_group = get_admin_group_id()

    # ── 1) Auto-registered users (deep-link flow) ──
    try:
        active_users = db_get_active_registered_users()
    except Exception as e:
        log(f"[EXPIRY] Could not load registered users: {e}")
        active_users = []

    for row in active_users:
        user_id  = row['userid']
        end_date = _parse_end_date(row['end_date'])
        if end_date is None:
            continue
        days = (end_date - today).days
        try:
            if days == 5:
                await _notify_user(context, user_id, _renew_message(row.get('plan_type', ''), 5))
            elif days == 3:
                await _notify_user(context, user_id, _renew_message(row.get('plan_type', ''), 3))
            elif days == 1 and admin_group:
                await _post_expiry_card(context, admin_group, "r", user_id,
                                        row.get('username', ''), row.get('plan_type', ''), end_date)
            elif days <= 0:
                await kick_and_deactivate(context, "r", user_id)
                await _notify_user(context, user_id,
                    "<b>Subscription Expired</b>\n\n"
                    "You have been removed from the group.\n"
                    f"To rejoin, renew your subscription — contact {get_support_contact()}")
        except Exception as e:
            log(f"[EXPIRY] Error on registered user {user_id}: {e}")

    # ── 2) Manually-added paid users (web panel) ──
    try:
        paid_users = db_get_all_paid_users()
    except Exception as e:
        log(f"[EXPIRY] Could not load paid users: {e}")
        paid_users = []

    for row in paid_users:
        if int(row.get('is_active', 0)) != 1:
            continue
        user_id  = row['user_id']
        end_date = _parse_end_date(row['end_date'])
        if end_date is None:
            continue
        days = (end_date - today).days
        try:
            if days == 5:
                await _notify_user(context, user_id, _renew_message(row.get('course', ''), 5))
            elif days == 3:
                await _notify_user(context, user_id, _renew_message(row.get('course', ''), 3))
            elif days == 1 and admin_group:
                await _post_expiry_card(context, admin_group, "p", user_id,
                                        row.get('username', ''), row.get('course', ''), end_date)
            elif days <= 0:
                await kick_and_deactivate(context, "p", user_id)
                await _notify_user(context, user_id,
                    "<b>Subscription Expired</b>\n\n"
                    "You have been removed from the group.\n"
                    f"To rejoin, renew your subscription — contact {get_support_contact()}")
        except Exception as e:
            log(f"[EXPIRY] Error on paid user {user_id}: {e}")
