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
    db_get_active_registered_users,
    db_get_user_by_userid,
    db_get_all_paid_users,
    db_get_paid_user,
    db_remove_registered_user,
    db_remove_paid_user,
    db_get_active_subs_by_userid,
    db_user_has_active_sub_for_group,
    db_get_courses_by_group_id,
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

async def _make_claim_invite(context: ContextTypes.DEFAULT_TYPE,
                             course: dict, unique_id: str, months: int) -> str | None:
    """
    First-time claim link for a UniqueId. It requires approval
    (creates_join_request) and encodes the claim in its name as 'c_<uid>_<months>'.
    The subscription is bound to whoever ACTUALLY joins (see _approve_claim), and
    the UniqueId is only consumed once a real join is approved — not on click.
    """
    group_id = course.get('group_id')
    if not group_id:
        return course.get('group_link') or None
    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=int(group_id),
            creates_join_request=True,
            name=f"c_{unique_id}_{months}",
        )
        return invite.invite_link
    except Exception as e:
        log(f"[CLAIM] create_chat_invite_link failed for group {group_id}, uid {unique_id}: {e}")
        return course.get('group_link') or None


async def _make_join_request_invite(context: ContextTypes.DEFAULT_TYPE,
                                    course: dict, user_id: int) -> str | None:
    """
    Create an invite link that requires admin approval (creates_join_request).
    Even if the user forwards this link, only an account that actually holds an
    active subscription for this group gets approved (see handle_join_request),
    so a shared link can never let an outsider in. Used for rejoin/recovery.
    """
    group_id = course.get('group_id')
    if not group_id:
        return course.get('group_link') or None
    try:
        link_id = str(uuid.uuid4())[:8]
        invite  = await context.bot.create_chat_invite_link(
            chat_id=int(group_id),
            creates_join_request=True,
            name=f"rejoin_{user_id}_{link_id}",
        )
        return invite.invite_link
    except Exception as e:
        log(f"[REJOIN] create_chat_invite_link failed for group {group_id}, user {user_id}: {e}")
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


async def _send_join_link(update: Update, course: dict, months: int,
                          end_date, invite: str | None):
    """Send a group join link + support contact. The link is approval-gated, so
    the user is admitted only after the bot approves their join request."""
    msg = get_display_message(course['course_name'], months, end_date)
    if invite:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Join Group", url=invite)]])
        await update.message.reply_text(
            msg + "\n\n👉 Tap to join — you'll be approved automatically.",
            reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(
            msg + f"\n\n⚠️ Couldn't create your invite link — contact {get_support_contact()}",
            parse_mode="HTML"
        )


async def _recover_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Blank /start (or any /start with no valid payload) → look the user up by
    their Telegram id. If they hold active (non-expired) subscriptions, hand
    them a rejoin link for every course group they're not already in. Otherwise
    point them at support. Rejoin links require approval, so they can't be
    shared to outsiders (see handle_join_request).
    """
    user_id = update.effective_user.id
    await update.message.reply_text("🔎 <b>Checking your details…</b>", parse_mode="HTML")

    try:
        subs = db_get_active_subs_by_userid(user_id)
    except Exception as e:
        log(f"[RECOVER] lookup failed for {user_id}: {e}")
        subs = []

    if not subs:
        await update.message.reply_text(
            "<b>No active subscription found.</b>\n\n"
            "We couldn't find an active subscription linked to your Telegram account.\n\n"
            f"If you believe this is a mistake, contact {get_support_contact()}",
            parse_mode="HTML"
        )
        return

    sent = 0
    for sub in subs:
        course = get_course_by_name(sub["course_name"])
        if not course:
            continue
        group_id = course.get("group_id")
        # Already inside this group → nothing to do.
        if group_id and await _is_group_member(context, group_id, user_id):
            continue
        invite = await _make_join_request_invite(context, course, user_id)
        if not invite:
            continue
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            f"Rejoin {course['course_name']}", url=invite)]])
        await update.message.reply_text(
            f"<b>{html.escape(course['course_name'])}</b>\n"
            "Tap below to rejoin — you'll be approved automatically.",
            reply_markup=kb, parse_mode="HTML"
        )
        sent += 1

    if sent == 0:
        await update.message.reply_text(
            "<b>You already have access ✅</b>\n\n"
            "You're already in all of your group(s).\n\n"
            f"Need help? Contact {get_support_contact()}",
            parse_mode="HTML"
        )


async def _approve_claim(context: ContextTypes.DEFAULT_TYPE, req, link_name: str):
    """
    First-time claim join (link named 'c_<uid>_<months>'). Bind the UniqueId and
    create the subscription for the account that ACTUALLY joins — not whoever
    clicked the bot. The UniqueId is consumed here, at the real join.
    """
    user_id  = req.from_user.id
    group_id = req.chat.id
    username = req.from_user.username or req.from_user.first_name

    try:
        _, unique_id, months_s = link_name.split("_", 2)
        months = max(1, int(months_s))
    except Exception:
        unique_id, months = None, 1

    # If the UniqueId was already consumed by a different account, refuse.
    claimed   = get_claimed_link(unique_id) if unique_id else None
    bound_uid = claimed.get('claimed_userid') if claimed else None
    if bound_uid is not None and bound_uid != user_id:
        try:
            await context.bot.decline_chat_join_request(chat_id=group_id, user_id=user_id)
            log(f"[CLAIM] declined {user_id} -> {group_id}: uid {unique_id} already used by {bound_uid}")
        except Exception as e:
            log(f"[CLAIM] decline error {user_id}/{group_id}: {e}")
        return

    try:
        await context.bot.approve_chat_join_request(chat_id=group_id, user_id=user_id)
    except Exception as e:
        log(f"[CLAIM] approve failed {user_id}/{group_id}: {e}")
        return

    # Record the real joiner as the subscriber (only if this is a brand-new bind).
    if bound_uid is None:
        courses     = db_get_courses_by_group_id(group_id)
        course_name = courses[0]['course_name'] if courses else ''
        today    = datetime.now().date()
        end_date = today + timedelta(days=30 * months)
        try:
            db_save_registered_user(
                userid=user_id,
                username=username,
                invite_link_id=str(uuid.uuid4())[:8],
                invite_link_url='',
                registration_date=str(today),
                end_date=str(end_date),
                plan_type=course_name,
                link_used=1,
            )
        except Exception as e:
            log(f"[CLAIM] could not save subscriber {user_id}: {e}")
        if unique_id:
            claim_link(unique_id, user_id)   # bind + consume the UniqueId now
        log(f"[CLAIM] uid={unique_id} bound to joiner {user_id} (@{username}) "
            f"course={course_name} ({months}m) expires={end_date}")


async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gate every join request. A first-time claim link binds the subscription to
    the real joiner; any other link (rejoin/recovery) is approved only if that
    account already holds an active subscription for the group. Either way a
    forwarded link is useless to an outsider.
    """
    req = update.chat_join_request
    if req is None:
        return
    user_id   = req.from_user.id
    group_id  = req.chat.id
    link_name = (req.invite_link.name if req.invite_link else "") or ""

    # First-time claim → bind to whoever joins.
    if link_name.startswith("c_"):
        await _approve_claim(context, req, link_name)
        return

    # Rejoin / recovery → must already be an active subscriber.
    try:
        if db_user_has_active_sub_for_group(user_id, group_id):
            await context.bot.approve_chat_join_request(chat_id=group_id, user_id=user_id)
            log(f"[JOINREQ] approved {user_id} -> {group_id}")
        else:
            await context.bot.decline_chat_join_request(chat_id=group_id, user_id=user_id)
            log(f"[JOINREQ] declined {user_id} -> {group_id} (no active sub)")
    except Exception as e:
        log(f"[JOINREQ] error for {user_id}/{group_id}: {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    # No deep-link payload → try to recover access for an existing subscriber.
    if not context.args:
        await _recover_access(update, context)
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

    # ── Bind-on-join model ──
    # A UniqueId is consumed only once someone ACTUALLY joins the group via the
    # bot link (claimed_userid is set at approval time, not at click). Until then
    # it stays open. Whoever joins becomes the bound subscriber recorded in the
    # DB — guaranteeing the DB user id always equals the real group member.
    claimed = get_claimed_link(unique_id)
    bound_uid = claimed.get('claimed_userid') if claimed else None

    if bound_uid is not None:
        # A real join already happened on this UniqueId → it's locked.
        if bound_uid == user_id:
            # The bound subscriber is back (e.g. they left the group).
            if await _is_group_member(context, course.get('group_id'), user_id):
                await update.message.reply_text(
                    "<b>You already have access ✅</b>\n\n"
                    "You're already a member of this group.\n\n"
                    f"Trouble? Contact {get_support_contact()}",
                    parse_mode="HTML"
                )
                return
            row = db_get_user_by_userid(user_id)
            end_date = row['end_date'] if row else _today_plus(months)
            invite = await _make_join_request_invite(context, course, user_id)
            await _send_join_link(update, course, months, end_date, invite)
        else:
            # Someone else already joined on this UniqueId → block.
            await update.message.reply_text(
                "<b>Link expired.</b>\n\n"
                "This access link has already been used by another account.\n\n"
                f"Contact {get_support_contact()}",
                parse_mode="HTML"
            )
        return

    # ── Not yet joined → hand out a claim link. The subscription is created and
    #    the UniqueId bound only when the join request is approved. ──
    invite = await _make_claim_invite(context, course, unique_id, months)
    log(f"[CLAIM] issued claim link uid={unique_id} -> {course['course_name']} ({months}m)")
    await _send_join_link(update, course, months, _today_plus(months), invite)


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
