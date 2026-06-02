import os
import pandas as pd
from typing import Final

# ─────────────────────────────────────────────
#  BOT CREDENTIALS
# ─────────────────────────────────────────────

TOKEN: Final = os.environ.get("BOT_TOKEN", '8357857623:AAH8uwRGnKmnaaH-RipXiCP5BPyE_bSKor4')
BOT_USERNAME: Final = os.environ.get("BOT_USERNAME", '@tesingt_04bot')

# ─────────────────────────────────────────────
#  GROUP / ADMIN IDs
#  All overridable via Render env vars so real values stay out of git
#  and hidden from the web panel.
# ─────────────────────────────────────────────
def _env_int(name: str, default: int) -> int:
    """Read an integer env var, falling back to default if unset/invalid."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default

ADMIN_GROUP_ID:       Final = _env_int("ADMIN_GROUP_ID", -12342343214)
ADMIN_USER_ID:        Final = _env_int("ADMIN_USER_ID", 8502504224)    # only this user can /startadmin in private chat
BOT_CREATOR_GROUP_ID: Final = _env_int("BOT_CREATOR_GROUP_ID", -1002345678901)  # BotCreaterGroup ID (backups)
BOT_CREATOR_USER_ID:  Final = _env_int("BOT_CREATOR_USER_ID", 1234567890)    # bot creator's Telegram user ID

# ─────────────────────────────────────────────
#  SUPPORT CONTACT
# ─────────────────────────────────────────────
SUPPORT_CONTACT: Final = '@helpsteno'

# ─────────────────────────────────────────────
#  FILE PATHS  (kept for compatibility; not used for storage)
# ─────────────────────────────────────────────
REGISTERED_USERS_FILE = "Data/RegisteredUsers.xlsx"
INVITE_LINKS_FILE     = "Data/InviteLinks.xlsx"
PAID_USERS_FILE       = "Data/PaidUsers.xlsx"
USER_JOIN_LOGS_FILE   = "Data/UserJoinLogs.xlsx"

# These are no longer backed by files — set to None to make accidental access obvious
COURSE_NAMES_FILE     = None
GUID_FILE             = None
MASTER_GROUPS_FILE    = None
COURSES_FILE          = None
PENDING_INVITES_FILE  = None

# ─────────────────────────────────────────────
#  MASTER GROUPS  — backed by PostgreSQL via db.py
# ─────────────────────────────────────────────

def get_admin_group_id() -> int | None:
    """Active admin group from DB. None if not set."""
    from .db import db_get_admin_group_id
    return db_get_admin_group_id()


def get_current_admin_group() -> dict | None:
    """Return {'group_id', 'group_name'} of the active admin group, or None."""
    from .db import db_get_current_admin_group
    return db_get_current_admin_group()


def set_admin_group_id(gid: int, group_name: str = ''):
    """Save admin group to DB."""
    from .db import db_set_admin_group
    db_set_admin_group(gid, group_name)


def remove_admin_group() -> bool:
    """Deactivate the current admin group. Returns True if one was removed."""
    from .db import db_remove_admin_group
    return db_remove_admin_group()


# ─────────────────────────────────────────────
#  MONTH CODES  (unique 5-6 char code -> months)
# ─────────────────────────────────────────────
MONTHS_CONSTANT: Final = {
    'poyww': 1,
    'lk9rt': 2,
    'xm4tw': 3,
    'bq7ns': 4,
    'rj2pv': 5,
    'hn8kf': 6,
    'vc3yx': 7,
    'wt6mz': 8,
    'gd5lb': 9,
    'mp1ej': 10,
    'fq9us': 11,
    'ak4wd': 12,
}

# Reverse lookup: month count -> month code
MONTHS_CODE_BY_COUNT: Final = {v: k for k, v in MONTHS_CONSTANT.items()}

def get_months(month_code: str) -> int:
    """Return month count for a given month code."""
    return MONTHS_CONSTANT.get(month_code, 1)

# ─────────────────────────────────────────────
#  SUBSCRIPTION PLANS
# ─────────────────────────────────────────────
SUBSCRIPTION_PLANS: Final = {
    'stemTreky': {
        'month_code': 'xm4tw',          # 3 months
        'code':       'stem_treky',
        'channel_id': -1003968942648,
    },
    'autmperm': {
        'month_code': 'poyww',          # 1 month
        'code':       'autm_perm',
        'channel_id': -1003815018227,
    },
    'stemluis': {
        'month_code': 'lk9rt',          # 2 months
        'code':       'stem_luis',
        'channel_id': -1003968942648,
    },
    'plan4': {
        'month_code': 'bq7ns',          # 4 months
        'code':       'plan_4',
        'channel_id': -1234567890,
    },
    'plan5': {
        'month_code': 'rj2pv',          # 5 months
        'code':       'plan_5',
        'channel_id': -1234567890,
    },
    'plan6': {
        'month_code': 'hn8kf',          # 6 months
        'code':       'plan_6',
        'channel_id': -1234567890,
    },
    'plan7': {
        'month_code': 'vc3yx',          # 7 months
        'code':       'plan_7',
        'channel_id': -1234567890,
    },
    'plan8': {
        'month_code': 'wt6mz',          # 8 months
        'code':       'plan_8',
        'channel_id': -1234567890,
    },
    'plan9': {
        'month_code': 'gd5lb',          # 9 months
        'code':       'plan_9',
        'channel_id': -1234567890,
    },
    'plan10': {
        'month_code': 'mp1ej',          # 10 months
        'code':       'plan_10',
        'channel_id': -1234567890,
    },
    'plan11': {
        'month_code': 'fq9us',          # 11 months
        'code':       'plan_11',
        'channel_id': -1234567890,
    },
    'plan12': {
        'month_code': 'ak4wd',          # 12 months
        'code':       'plan_12',
        'channel_id': -1234567890,
    },
}

# code -> plan_key reverse lookup (used in /start deep-link)
CODE_TO_PLAN_KEY: Final = {v['code']: k for k, v in SUBSCRIPTION_PLANS.items()}

# month_code -> plan_key reverse lookup
MONTH_CODE_TO_PLAN_KEY: Final = {v['month_code']: k for k, v in SUBSCRIPTION_PLANS.items()}

# plan_key -> channel_id (flat lookup)
CHANNEL_MAPPING: Final = {k: v['channel_id'] for k, v in SUBSCRIPTION_PLANS.items()}

# ─────────────────────────────────────────────
#  COURSE NAMES  (default display info)
# ─────────────────────────────────────────────
_DEFAULT_COURSES = [
    {'plan_key': 'stemTreky', 'course_name': 'Treaky Course',  'emoji': '🎓', 'description': 'Full Stem Treaky program'},
    {'plan_key': 'autmperm',  'course_name': 'Premium',        'emoji': '📚', 'description': 'Autumn Premium access'},
    {'plan_key': 'stemluis',  'course_name': 'Luis Course',    'emoji': '🌟', 'description': 'Stem Luis program'},
    {'plan_key': 'plan4',     'course_name': 'Plan 4',         'emoji': '💎', 'description': '4-month exclusive plan'},
    {'plan_key': 'plan5',     'course_name': 'Plan 5',         'emoji': '🚀', 'description': '5-month exclusive plan'},
    {'plan_key': 'plan6',     'course_name': 'Plan 6',         'emoji': '🏆', 'description': '6-month exclusive plan'},
    {'plan_key': 'plan7',     'course_name': 'Plan 7',         'emoji': '⭐', 'description': '7-month exclusive plan'},
    {'plan_key': 'plan8',     'course_name': 'Plan 8',         'emoji': '✨', 'description': '8-month exclusive plan'},
    {'plan_key': 'plan9',     'course_name': 'Plan 9',         'emoji': '💫', 'description': '9-month exclusive plan'},
    {'plan_key': 'plan10',    'course_name': 'Plan 10',        'emoji': '🎯', 'description': '10-month exclusive plan'},
    {'plan_key': 'plan11',    'course_name': 'Plan 11',        'emoji': '🌈', 'description': '11-month exclusive plan'},
    {'plan_key': 'plan12',    'course_name': 'Plan 12',        'emoji': '👑', 'description': '12-month exclusive plan'},
]

# Build a quick lookup dict so get_course_info doesn't need file I/O
_COURSE_INFO_MAP = {row['plan_key']: row for row in _DEFAULT_COURSES}


def init_course_names_file():
    """No-op — kept for backward compatibility (subscription.py imports this)."""
    pass


def get_course_info(plan_key: str) -> dict:
    """Return course name, emoji, description for a plan_key."""
    if plan_key in _COURSE_INFO_MAP:
        return dict(_COURSE_INFO_MAP[plan_key])
    return {'course_name': plan_key, 'emoji': '📦', 'description': ''}


# ─────────────────────────────────────────────
#  DISPLAY MESSAGE TEMPLATE
# ─────────────────────────────────────────────
def get_display_message(plan_key: str, end_date) -> str:
    """Welcome message sent to user after successful subscription."""
    plan        = SUBSCRIPTION_PLANS[plan_key]
    month_code  = plan['month_code']
    months      = get_months(month_code)
    course      = get_course_info(plan_key)

    return (
        f"{course['emoji']} <b>Welcome to {course['course_name']}!</b>\n\n"
        f"📖 <b>Course:</b> {course['course_name']}\n"
        f"📅 <b>Duration:</b> {months} Month{'s' if months > 1 else ''}\n"
        f"⏰ <b>Access Until:</b> {end_date}\n\n"
        f"📝 {course['description']}\n\n"
        f"🎯 Click below to join — this link is <b>one-time use only</b>."
    )

def get_plan_display_label(plan_key: str) -> str:
    """Short label for inline keyboard buttons."""
    plan        = SUBSCRIPTION_PLANS[plan_key]
    month_code  = plan['month_code']
    months      = get_months(month_code)
    course      = get_course_info(plan_key)
    return f"{course['emoji']} {course['course_name']} ({months} Month{'s' if months > 1 else ''})"


# ─────────────────────────────────────────────
#  GUID SYSTEM — backed by PostgreSQL via db.py
# ─────────────────────────────────────────────

# verify_guid return codes
GUID_OK        = 'ok'
GUID_USED      = 'used'
GUID_NOT_FOUND = 'not_found'


def build_start_link(guid: str, plan_key: str, month_code: str) -> str:
    """Return the full Telegram deep-link for a GUID."""
    param = f"{guid}_{plan_key}_{month_code}"
    return f"https://t.me/{BOT_USERNAME.lstrip('@')}?start={param}"


def parse_start_param(raw: str) -> tuple[str, str, str] | None:
    """
    Parse '?start=' value -> (guid, plan_key, month_code).
    Format: guid_<anything>_monthCode  (last segment = monthCode).
    """
    parts = raw.split('_')
    if len(parts) < 2:
        return None
    guid       = parts[0]
    month_code = parts[-1]
    if not guid or month_code not in MONTHS_CONSTANT:
        return None
    plan_key = MONTH_CODE_TO_PLAN_KEY.get(month_code)
    if not plan_key:
        return None
    return guid, plan_key, month_code


def verify_guid(guid: str) -> str:
    """Check GUID validity. Does NOT mark it used."""
    from .db import db_verify_guid
    return db_verify_guid(guid)


def mark_guid_used(guid: str):
    """Mark a GUID as used after successful registration."""
    from .db import db_mark_guid_used
    db_mark_guid_used(guid)


def generate_guids(plan_key: str, month_code: str, count: int = 100) -> int:
    """Generate `count` GUIDs and store in DB. Returns count inserted."""
    if plan_key not in SUBSCRIPTION_PLANS:
        raise ValueError(f"Unknown plan_key: {plan_key}")
    if month_code not in MONTHS_CONSTANT:
        raise ValueError(f"Unknown month_code: {month_code}")
    from .db import db_generate_guids
    return db_generate_guids(count)


# ─────────────────────────────────────────────
#  GROUPS  — backed by PostgreSQL via db.py
# ─────────────────────────────────────────────

def get_group_for_course(course_name: str) -> int | None:
    """Return group_id whose course_name matches (case-insensitive). None if not found."""
    from .db import db_get_group_for_course
    return db_get_group_for_course(course_name)


def save_group(group_id: int, course_name: str, group_name: str = ''):
    """Add or update a course sub-group in DB."""
    from .db import db_save_group
    db_save_group(group_id, course_name, group_name)


# ─────────────────────────────────────────────
#  COURSES  — backed by PostgreSQL via db.py
# ─────────────────────────────────────────────

def load_courses() -> pd.DataFrame:
    from .db import db_load_courses
    return db_load_courses()


def save_course(course_name: str):
    """Append a new course to DB."""
    from .db import db_save_course
    db_save_course(course_name)


def delete_course(course_id: int):
    """Remove a course from DB by id."""
    from .db import db_delete_course
    db_delete_course(course_id)


# ─────────────────────────────────────────────
#  PENDING INVITES  — backed by PostgreSQL via db.py
# ─────────────────────────────────────────────

def save_pending_invite(invite_link: str, link_name: str, course_name: str,
                        months: int, start_date: str, end_date: str):
    from .db import db_save_pending_invite
    db_save_pending_invite(invite_link, link_name, course_name, months, start_date, end_date)


def get_pending_invite(invite_link: str) -> dict | None:
    """Return pending invite data for a link, or None if not found / already used."""
    from .db import db_get_pending_invite
    return db_get_pending_invite(invite_link)


def mark_pending_invite_used(invite_link: str):
    from .db import db_mark_pending_invite_used
    db_mark_pending_invite_used(invite_link)
