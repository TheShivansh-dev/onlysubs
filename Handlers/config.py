import os
import re
import pandas as pd
from typing import Final, Optional

# ─────────────────────────────────────────────
#  BOT CREDENTIALS
# ─────────────────────────────────────────────

TOKEN: Final = os.environ.get("BOT_TOKEN", '8357857623:AAH8uwRGnKmnaaH-RipXiCP5BPyE_bSKor4')
BOT_USERNAME: Final = os.environ.get("BOT_USERNAME", '@tesingt_04bot')

# ─────────────────────────────────────────────
#  GROUP / ADMIN IDs
#  All overridable via env vars so real values stay out of git.
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

# Only the backup target group still comes from env (optional).
# Admin / superadmin recognition now comes from the DB (panel accounts' tg_id).
BOT_CREATOR_GROUP_ID: Final = _env_int("BOT_CREATOR_GROUP_ID", -1002345678901)  # backups


def is_panel_member(tg_id: int) -> bool:
    """True if this Telegram id belongs to a panel account (owner/superadmin/admin)."""
    from .db import db_is_panel_member
    return db_is_panel_member(tg_id)

# ─────────────────────────────────────────────
#  SUPPORT CONTACT  (sent to users so they can reach a human)
#  Stored in the DB (bot_settings) so a superadmin can change it from the
#  website without a redeploy. Falls back to @helpsteno if unset.
# ─────────────────────────────────────────────
def get_support_contact() -> str:
    from .db import db_get_setting
    return db_get_setting('SUPPORT_CONTACT', '@helpsteno') or '@helpsteno'

# ─────────────────────────────────────────────
#  ADMIN GROUP  — backed by PostgreSQL via db.py
# ─────────────────────────────────────────────

def get_admin_group_id() -> Optional[int]:
    """Active admin group from DB. None if not set."""
    from .db import db_get_admin_group_id
    return db_get_admin_group_id()


def get_current_admin_group() -> Optional[dict]:
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
#  COURSE CODE  — first two letters of every word in the course name
#  e.g. "Stem Treaky Course" -> "StTrCo"
# ─────────────────────────────────────────────

def make_course_code(course_name: str) -> str:
    """First two characters of each word, joined, case preserved."""
    words = re.split(r'\s+', (course_name or '').strip())
    return ''.join(w[:2] for w in words if w)


# ─────────────────────────────────────────────
#  DEEP-LINK PARSING
#  Format:  <UniqueId>_<CourseCode>_<Months>
#  e.g.     a1b2c3d4_StTrCo_3
# ─────────────────────────────────────────────

def parse_start_param(raw: str) -> Optional[tuple[str, str, int]]:
    """
    Parse the '?start=' value into (unique_id, course_code, months).
    Returns None if the shape is invalid (months must be a positive int).
    """
    if not raw:
        return None
    parts = raw.split('_')
    if len(parts) < 3:
        return None
    unique_id  = parts[0]
    months_str = parts[-1]
    course_code = '_'.join(parts[1:-1])   # course codes never contain '_', but be safe
    if not unique_id or not course_code or not months_str.isdigit():
        return None
    # UniqueId must be exactly 8 characters — no more, no less.
    if len(unique_id) != 8:
        return None
    months = int(months_str)
    if months < 1:
        return None
    return unique_id, course_code, months


def build_start_link(unique_id: str, course_code: str, months: int) -> str:
    """Construct the full Telegram deep link for a course/UniqueId."""
    param = f"{unique_id}_{course_code}_{months}"
    return f"https://t.me/{BOT_USERNAME.lstrip('@')}?start={param}"


# ─────────────────────────────────────────────
#  COURSE LOOKUP  — backed by PostgreSQL via db.py
# ─────────────────────────────────────────────

def get_course_by_code(course_code: str) -> Optional[dict]:
    """Return {id, course_name, course_code, group_link, group_id} or None."""
    from .db import db_get_course_by_code
    return db_get_course_by_code(course_code)


def get_course_by_name(course_name: str) -> Optional[dict]:
    """Return {id, course_name, course_code, group_link, group_id} or None."""
    from .db import db_get_course_by_name
    return db_get_course_by_name(course_name)


def load_courses() -> pd.DataFrame:
    from .db import db_load_courses
    return db_load_courses()


def save_course(course_name: str, group_link: str = "", group_id: Optional[int] = None):
    """Save a course; its code is auto-derived from the name."""
    from .db import db_save_course
    db_save_course(course_name, make_course_code(course_name), group_link, group_id)


def delete_course(course_id: int):
    from .db import db_delete_course
    db_delete_course(course_id)


# ─────────────────────────────────────────────
#  CLAIMED UNIQUE-IDS  (Approach A: trust-on-first-use)
# ─────────────────────────────────────────────

def get_claimed_link(unique_id: str) -> Optional[dict]:
    """Return {'claimed_userid', 'claimed_date'} if used before, else None."""
    from .db import db_get_claimed_link
    return db_get_claimed_link(unique_id)


def claim_link(unique_id: str, userid: int):
    """Bind a UniqueId to the Telegram user who first used it."""
    from .db import db_claim_link
    db_claim_link(unique_id, userid)


# ─────────────────────────────────────────────
#  DISPLAY MESSAGE
# ─────────────────────────────────────────────

def get_display_message(course_name: str, months: int, end_date) -> str:
    """Welcome message sent to the user after a successful subscription."""
    return (
        f"🎉 <b>Welcome to {course_name}!</b>\n\n"
        f"📅 <b>Duration:</b> {months} Month{'s' if months > 1 else ''}\n"
        f"⏰ <b>Access Until:</b> {end_date}\n\n"
        f"🎯 Tap below to join the group.\n\n"
        f"❓ Need help? Contact {get_support_contact()}"
    )
