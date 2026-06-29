from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .config import (
    get_support_contact,
    get_admin_group_id,
    get_course_by_code,
    get_course_by_name,
    get_claimed_link,
    claim_link,
    get_pending_link,
    set_pending_link,
    get_display_message,
    parse_start_param,
)
from .db import (
    db_save_registered_user,
    db_save_invite_link,
    db_reactivate_registered_user,
    db_get_active_registered_users,
    db_get_user_by_userid,
    db_get_all_paid_users,
    db_get_paid_user,
    db_remove_registered_user,
    db_remove_paid_user,
    db_get_active_subs_by_userid,
    db_get_courses_by_group_id,
    db_add_log,
    db_clear_logs,
    db_get_setting,
    db_set_setting,
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

async def _make_member_invite(context: ContextTypes.DEFAULT_TYPE,
                              course: dict, name: str) -> str | None:
    """
    Create a fresh single-use (member_limit=1) invite link the user can tap to
    join the group directly. The link name lets the chat_member handler tell who
    actually joined and bind the subscription to them. Falls back to a static
    group_link if the course has no numeric group_id.
    """
    group_id = course.get('group_id')
    if not group_id:
        return course.get('group_link') or None
    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=int(group_id),
            member_limit=1,
            name=name[:32],
        )
        return invite.invite_link
    except Exception as e:
        log(f"[LINK] create_chat_invite_link failed for group {group_id} ({name}): {e}")
        return course.get('group_link') or None


async def _revoke_invite(context: ContextTypes.DEFAULT_TYPE, group_id, link_url: str):
    """Revoke a used invite link so it is fully expired and can never be reused."""
    if not group_id or not link_url:
        return
    try:
        await context.bot.revoke_chat_invite_link(chat_id=int(group_id), invite_link=link_url)
        log(f"[LINK] revoked used invite link in {group_id}")
    except Exception as e:
        log(f"[LINK] could not revoke invite link in {group_id}: {e}")


async def _make_claim_invite(context: ContextTypes.DEFAULT_TYPE,
                             course: dict, unique_id: str, months: int) -> str | None:
    """
    First-time claim link for a UniqueId. Re-uses the same link on every click
    (stored on the guids row) until someone actually joins, so a UniqueId never
    mints multiple shareable links. Named 'c_<uid>_<months>' so the chat_member
    handler can bind the subscription to whoever really joins.
    """
    existing = get_pending_link(unique_id)
    if existing:
        return existing
    invite = await _make_member_invite(context, course, f"c_{unique_id}_{months}")
    if invite:
        try:
            set_pending_link(unique_id, invite)
        except Exception as e:
            log(f"[CLAIM] could not store pending link for {unique_id}: {e}")
    return invite


async def _make_rejoin_invite(context: ContextTypes.DEFAULT_TYPE,
                              course: dict, user_id: int) -> str | None:
    """A single-use link for a bound subscriber to re-enter a group they left."""
    link_id = str(uuid.uuid4())[:8]
    return await _make_member_invite(context, course, f"rj_{user_id}_{link_id}")


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
                          end_date, invite: str | None, start_date=None):
    """Send a one-tap group join link + support contact (direct join)."""
    msg = get_display_message(course['course_name'], months, end_date, start_date)
    if invite:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Join Group", url=invite)]])
        await update.message.reply_text(
            msg + "\n\n👉 Tap to join the group.",
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
    them a single-use rejoin link for every course group they're not already in.
    Otherwise point them at support.
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
        invite = await _make_rejoin_invite(context, course, user_id)
        if not invite:
            continue
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            f"Rejoin {course['course_name']}", url=invite)]])
        await update.message.reply_text(
            f"<b>{html.escape(course['course_name'])}</b>\n"
            "Tap below to rejoin the group.",
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


_JOINED_FROM = ("left", "kicked")
_JOINED_TO   = ("member", "administrator", "creator", "owner", "restricted")


async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fires when someone's membership in a group changes (bot must be an admin).
    When a user joins via a first-time claim link (named 'c_<uid>_<months>'), we
    bind the UniqueId and create the subscription for the account that ACTUALLY
    joined — not whoever clicked the bot. This is what stores 'which link → who
    joined' and keeps the DB user == the real group member. The UniqueId is
    consumed only here, at the real join.
    """
    cmu = update.chat_member
    if cmu is None:
        return
    old = cmu.old_chat_member.status
    new = cmu.new_chat_member.status
    if not (old in _JOINED_FROM and new in _JOINED_TO):
        return   # not a fresh join

    user      = cmu.new_chat_member.user
    group_id  = cmu.chat.id
    invite    = cmu.invite_link
    link_name = (invite.name if invite else "") or ""
    log(f"[JOIN] {user.id} (@{user.username}) joined {group_id} via '{link_name or 'unknown link'}'")

    if not link_name.startswith("c_"):
        # Rejoin / manual join → nothing new to bind, but if this is a known
        # subscriber who was removed (and is still within date), bring them back
        # to active so the panel reflects that they're in the group again.
        try:
            if db_reactivate_registered_user(user.id):
                log(f"[REJOIN] re-activated subscriber {user.id} on join to {group_id}")
        except Exception as e:
            log(f"[REJOIN] reactivate failed for {user.id}: {e}")
        # A bot-issued rejoin link is single-use → revoke it now it's been used.
        if link_name.startswith("rj_") and invite:
            await _revoke_invite(context, group_id, invite.invite_link)
        return

    try:
        _, unique_id, months_s = link_name.split("_", 2)
        months = max(1, int(months_s))
    except Exception:
        return

    claimed = get_claimed_link(unique_id)
    if claimed and claimed.get('claimed_userid') is not None:
        return   # UniqueId already bound to its first real joiner

    courses     = db_get_courses_by_group_id(group_id)
    course_name = courses[0]['course_name'] if courses else ''
    today    = datetime.now().date()
    end_date = today + timedelta(days=30 * months)
    try:
        db_save_registered_user(
            userid=user.id,
            username=user.username or user.first_name,
            invite_link_id=link_name,
            invite_link_url=(invite.invite_link if invite else ''),
            registration_date=str(today),
            end_date=str(end_date),
            plan_type=course_name,
            link_used=1,
        )
    except Exception as e:
        log(f"[CLAIM] could not save subscriber {user.id}: {e}")
    try:
        db_save_invite_link(link_name, user.id, invite.invite_link if invite else '')
    except Exception as e:
        log(f"[CLAIM] could not record link->user map: {e}")
    claim_link(unique_id, user.id)   # bind + consume the UniqueId now
    # The claim link is single-use → revoke it so it's fully expired, and clear
    # the stored pending link so it can never be handed out again.
    if invite:
        await _revoke_invite(context, group_id, invite.invite_link)
    try:
        set_pending_link(unique_id, "")
    except Exception:
        pass
    log(f"[CLAIM] uid={unique_id} bound to joiner {user.id} "
        f"course={course_name} ({months}m) expires={end_date}")


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
            # Only let them back in while the subscription date is still valid.
            ed = _parse_end_date(end_date)
            if ed is not None and (ed - datetime.now().date()).days < 0:
                await update.message.reply_text(
                    "<b>Subscription expired.</b>\n\n"
                    "Your access for this course has ended. Please renew it.\n\n"
                    f"To renew, contact {get_support_contact()}",
                    parse_mode="HTML"
                )
                return
            invite = await _make_rejoin_invite(context, course, user_id)
            start = row.get('registration_date') if row else None
            await _send_join_link(update, course, months, end_date, invite, start)
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
    #    the UniqueId bound only when the user actually joins (chat_member). ──
    invite = await _make_claim_invite(context, course, unique_id, months)
    log(f"[CLAIM] issued claim link uid={unique_id} -> {course['course_name']} ({months}m)")
    await _send_join_link(update, course, months, _today_plus(months), invite,
                          datetime.now().date())


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


def _course_website(course_name: str) -> str:
    """Return the course's renewal website link (or '' if none set)."""
    if not course_name:
        return ""
    course = get_course_by_name(str(course_name))
    return (course.get('website_url') or '').strip() if course else ""


def _renew_message(course_name: str, days: int, website_url: str = "") -> str:
    """Reminder sent to the user 3 days, 2 days and 1 day (final) before removal."""
    when = "in less than 24 hours" if days <= 1 else f"in <b>{days} days</b>"
    msg = (
        f"<b>Subscription Expiring Soon!</b>\n\n"
        f"Your <b>{html.escape(str(course_name))}</b> access expires {when}.\n\n"
        f"Please renew the same course to keep your access.\n"
    )
    if website_url:
        msg += f"\n🔗 <b>Renew here:</b> {html.escape(website_url)}\n"
    msg += f"\nTo renew, contact {get_support_contact()}"
    return msg


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
    banned = False
    try:
        await context.bot.ban_chat_member(chat_id=int(group_id), user_id=user_id)
        banned = True
        log(f"[KICK] Removed user {user_id} from group {group_id}")
        return True
    except Exception as e:
        # Surface the EXACT Telegram reason on the panel Logs page. Common ones:
        #   "not enough rights"        → bot's "Ban users" admin toggle is off
        #   "user is an administrator" → can't kick an admin (demote them first)
        #   can't kick the group owner → the creator can never be removed
        #   "chat not found"           → wrong group_id on the course
        log(f"[KICK] Could not remove user {user_id} from {group_id}: {e}")
        try:
            db_add_log("system", "kick_error", f"userid={user_id} group={group_id}: {e}")
        except Exception:
            pass
        return False
    finally:
        # Lift the ban so the user is never left blocked — only if we banned them.
        if banned:
            try:
                await context.bot.unban_chat_member(
                    chat_id=int(group_id), user_id=user_id, only_if_banned=True)
            except Exception as e:
                log(f"[KICK] Could not unban user {user_id} from {group_id}: {e}")


async def kick_and_deactivate(context: ContextTypes.DEFAULT_TYPE, kind: str, user_id: int) -> bool:
    """
    Remove a user from their group and, ONLY if the kick succeeded, mark them
    inactive in the DB. If the kick fails the row stays active so the next run
    retries it (and the admin still sees them) — we never falsely mark someone
    removed while they're still sitting in the group.
    """
    group_id = resolve_group_id(kind, user_id)
    kicked   = await kick_user(context, group_id, user_id)
    try:
        if kicked:
            if kind == "r":
                db_remove_registered_user(user_id, str(datetime.now().date()))
            else:
                db_remove_paid_user(user_id)
            db_add_log("system", "expiry_remove", f"userid={user_id} group={group_id}")
        else:
            reason = "no group_id (course/group not set)" if not group_id else \
                     "kick failed — see the kick_error log above (bot's Ban-users right, " \
                     "user is an admin/owner, or wrong group_id)"
            db_add_log("system", "expiry_remove_failed",
                       f"userid={user_id} group={group_id} — {reason}")
    except Exception:
        pass
    return kicked


async def _notify_removal_failed(context: ContextTypes.DEFAULT_TYPE,
                                 admin_group, user_id: int, course_name: str):
    """Tell the admin group when an expired user couldn't be auto-removed."""
    try:
        await context.bot.send_message(
            chat_id=admin_group,
            text=(
                "⚠️ <b>Auto-removal failed</b>\n\n"
                f"User ID: <code>{user_id}</code>\n"
                f"Course: {html.escape(str(course_name))}\n\n"
                "They were marked expired but could NOT be removed from the group. "
                "Check that the bot is an admin there (with 'Ban users') and that "
                "the user isn't the group owner — then remove them manually."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        log(f"[EXPIRY] could not post removal-failure note for {user_id}: {e}")


_LOG_RETENTION_DAYS = 10


async def _maybe_clear_logs():
    """
    Wipe the audit log every 10 days, tied to the daily expiry run. Tracks the
    last clear date in bot_settings so it fires at most once per 10 days.
    """
    try:
        today = datetime.now().date()
        last_raw = db_get_setting("LAST_LOG_CLEAR", "")
        last = None
        if last_raw:
            try:
                last = datetime.strptime(last_raw, "%Y-%m-%d").date()
            except ValueError:
                last = None
        if last is None or (today - last).days >= _LOG_RETENTION_DAYS:
            n = db_clear_logs()
            db_set_setting("LAST_LOG_CLEAR", str(today))
            log(f"[LOGS] cleared {n} audit log rows (every-{_LOG_RETENTION_DAYS}-day housekeeping)")
    except Exception as e:
        log(f"[LOGS] auto-clear failed: {e}")


async def check_subscription_expiry(context: ContextTypes.DEFAULT_TYPE):
    today       = datetime.now().date()
    admin_group = get_admin_group_id()

    # Housekeeping first, so this run's own diagnostics survive the wipe.
    await _maybe_clear_logs()

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
            if days in (3, 2):
                cname = row.get('plan_type', '')
                await _notify_user(context, user_id,
                                   _renew_message(cname, days, _course_website(cname)))
            elif days == 1:
                # Final reminder to the user + Save/Remove card to the admin group.
                cname = row.get('plan_type', '')
                await _notify_user(context, user_id,
                                   _renew_message(cname, 1, _course_website(cname)))
                if admin_group:
                    await _post_expiry_card(context, admin_group, "r", user_id,
                                            row.get('username', ''), cname, end_date)
            elif days <= 0:
                kicked = await kick_and_deactivate(context, "r", user_id)
                if kicked:
                    await _notify_user(context, user_id,
                        "<b>Subscription Expired</b>\n\n"
                        "You have been removed from the group.\n"
                        f"To rejoin, renew your subscription — contact {get_support_contact()}")
                elif admin_group:
                    await _notify_removal_failed(context, admin_group, user_id, row.get('plan_type', ''))
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
            if days in (3, 2):
                cname = row.get('course', '')
                await _notify_user(context, user_id,
                                   _renew_message(cname, days, _course_website(cname)))
            elif days == 1:
                cname = row.get('course', '')
                await _notify_user(context, user_id,
                                   _renew_message(cname, 1, _course_website(cname)))
                if admin_group:
                    await _post_expiry_card(context, admin_group, "p", user_id,
                                            row.get('username', ''), cname, end_date)
            elif days <= 0:
                kicked = await kick_and_deactivate(context, "p", user_id)
                if kicked:
                    await _notify_user(context, user_id,
                        "<b>Subscription Expired</b>\n\n"
                        "You have been removed from the group.\n"
                        f"To rejoin, renew your subscription — contact {get_support_contact()}")
                elif admin_group:
                    await _notify_removal_failed(context, admin_group, user_id, row.get('course', ''))
        except Exception as e:
            log(f"[EXPIRY] Error on paid user {user_id}: {e}")
