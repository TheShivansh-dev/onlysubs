"""
Handlers/db.py
PostgreSQL data layer — replaces all Excel I/O.
Uses psycopg2 ThreadedConnectionPool for thread-safe connections.
DATABASE_URL must be set as an environment variable.
"""

import os
import uuid
import contextlib
from datetime import datetime, date
from typing import Optional

import psycopg2
from psycopg2 import pool
import pandas as pd

# ─────────────────────────────────────────────
#  Connection pool
# ─────────────────────────────────────────────

_pool: Optional[pool.ThreadedConnectionPool] = None


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get(
            "DATABASE_URL",
            "postgresql://neondb_owner:npg_dM18BclknOqS@ep-frosty-sea-aqbowrbl.c-8.us-east-1.aws.neon.tech/neondb?sslmode=require"
        )
        if not url:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _pool = pool.ThreadedConnectionPool(1, 20, url)
    return _pool


@contextlib.contextmanager
def get_conn():
    """Thread-safe context manager that returns a psycopg2 connection from the pool."""
    p = _get_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


# ─────────────────────────────────────────────
#  Schema initialisation
# ─────────────────────────────────────────────

def init_schema():
    """Run schema.sql against the database (idempotent — uses IF NOT EXISTS)."""
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


# ─────────────────────────────────────────────
#  Master Groups
# ─────────────────────────────────────────────

def db_get_admin_group_id() -> Optional[int]:
    """Return the active admin group_id, or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT group_id FROM master_groups WHERE type='admin' AND is_active=1 LIMIT 1"
            )
            row = cur.fetchone()
    return int(row[0]) if row else None


def db_get_current_admin_group() -> Optional[dict]:
    """Return {'group_id', 'group_name'} or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT group_id, group_name FROM master_groups "
                "WHERE type='admin' AND is_active=1 LIMIT 1"
            )
            row = cur.fetchone()
    if not row:
        return None
    return {"group_id": int(row[0]), "group_name": str(row[1] or "")}


def db_set_admin_group(gid: int, group_name: str = ""):
    """Insert or activate an admin group entry."""
    today = str(date.today())
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Check existing
            cur.execute(
                "SELECT id FROM master_groups WHERE group_id=%s AND type='admin'", (gid,)
            )
            row = cur.fetchone()
            if row:
                if group_name:
                    cur.execute(
                        "UPDATE master_groups SET is_active=1, group_name=%s WHERE id=%s",
                        (group_name, row[0]),
                    )
                else:
                    cur.execute(
                        "UPDATE master_groups SET is_active=1 WHERE id=%s", (row[0],)
                    )
            else:
                cur.execute(
                    "INSERT INTO master_groups (group_id, group_name, type, course_name, added_date, is_active) "
                    "VALUES (%s, %s, 'admin', '', %s, 1)",
                    (gid, group_name, today),
                )


def db_remove_admin_group() -> bool:
    """Deactivate the current admin group. Returns True if one was removed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE master_groups SET is_active=0 WHERE type='admin' AND is_active=1"
            )
            return cur.rowcount > 0


def db_get_group_for_course(course_name: str) -> Optional[int]:
    """Return group_id whose course_name matches (case-insensitive). None if not found."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT group_id FROM master_groups "
                "WHERE type='course' AND is_active=1 AND LOWER(TRIM(course_name))=LOWER(TRIM(%s)) "
                "LIMIT 1",
                (course_name,),
            )
            row = cur.fetchone()
    return int(row[0]) if row else None


def db_save_group(group_id: int, course_name: str, group_name: str = ""):
    """Add or update a course sub-group."""
    today = str(date.today())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM master_groups WHERE group_id=%s AND type='course'", (group_id,)
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE master_groups SET course_name=%s, added_date=%s, is_active=1 "
                    "WHERE id=%s",
                    (course_name, today, row[0]),
                )
                if group_name:
                    cur.execute(
                        "UPDATE master_groups SET group_name=%s WHERE id=%s", (group_name, row[0])
                    )
            else:
                cur.execute(
                    "INSERT INTO master_groups (group_id, group_name, type, course_name, added_date, is_active) "
                    "VALUES (%s, %s, 'course', %s, %s, 1)",
                    (group_id, group_name, course_name, today),
                )


def db_get_all_groups() -> list[dict]:
    """Return all groups as list of dicts."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, group_id, group_name, type, course_name, added_date, is_active "
                "FROM master_groups ORDER BY id"
            )
            cols = ["id", "group_id", "group_name", "type", "course_name", "added_date", "is_active"]
            rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


# ─────────────────────────────────────────────
#  Courses
# ─────────────────────────────────────────────

_COURSE_COLS = ["id", "course_name", "course_code", "group_link", "group_id"]


def db_load_courses() -> pd.DataFrame:
    """Return courses table as a DataFrame."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, course_name, course_code, group_link, group_id "
                "FROM courses ORDER BY id"
            )
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=_COURSE_COLS)
    return pd.DataFrame(rows, columns=_COURSE_COLS)


def db_save_course(course_name: str, course_code: str = "",
                   group_link: str = "", group_id: Optional[int] = None):
    """Insert a new course (or update group info if the code already exists)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if course_code:
                cur.execute(
                    "SELECT id FROM courses WHERE LOWER(course_code)=LOWER(%s) LIMIT 1",
                    (course_code,),
                )
                existing = cur.fetchone()
            else:
                existing = None
            if existing:
                cur.execute(
                    "UPDATE courses SET course_name=%s, group_link=%s, group_id=%s WHERE id=%s",
                    (course_name, group_link, group_id, existing[0]),
                )
            else:
                cur.execute(
                    "INSERT INTO courses (course_name, course_code, group_link, group_id) "
                    "VALUES (%s, %s, %s, %s)",
                    (course_name, course_code, group_link, group_id),
                )


def db_delete_course(course_id: int):
    """Delete a course by id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM courses WHERE id=%s", (course_id,))


def db_get_course_by_code(course_code: str) -> Optional[dict]:
    """Look up a course by its code (case-insensitive)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, course_name, course_code, group_link, group_id "
                "FROM courses WHERE LOWER(course_code)=LOWER(%s) LIMIT 1",
                (course_code,),
            )
            row = cur.fetchone()
    return dict(zip(_COURSE_COLS, row)) if row else None


def db_get_course_by_name(course_name: str) -> Optional[dict]:
    """Look up a course by its name (case-insensitive)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, course_name, course_code, group_link, group_id "
                "FROM courses WHERE LOWER(TRIM(course_name))=LOWER(TRIM(%s)) LIMIT 1",
                (course_name,),
            )
            row = cur.fetchone()
    return dict(zip(_COURSE_COLS, row)) if row else None


def db_get_courses_by_group_id(group_id: int) -> list[dict]:
    """All courses mapped to a given numeric group id (usually one)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, course_name, course_code, group_link, group_id "
                "FROM courses WHERE group_id=%s",
                (group_id,),
            )
            rows = cur.fetchall()
    return [dict(zip(_COURSE_COLS, r)) for r in rows]


def db_get_active_subs_by_userid(user_id: int) -> list[dict]:
    """
    Every ACTIVE (non-expired) subscription this Telegram user holds, across
    both the deep-link 'registered_users' and the panel 'paid_users' tables.
    A NULL end_date counts as active (no expiry). Returns
    [{course_name, end_date, source}] deduped by course name.
    """
    out: dict[str, dict] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT plan_type, end_date FROM registered_users "
                "WHERE userid=%s AND isremoved=0 "
                "AND (end_date IS NULL OR end_date >= CURRENT_DATE)",
                (user_id,),
            )
            for cname, end in cur.fetchall():
                if cname:
                    out[str(cname).strip()] = {"course_name": str(cname).strip(),
                                               "end_date": end, "source": "r"}
            cur.execute(
                "SELECT course, end_date FROM paid_users "
                "WHERE user_id=%s AND is_active=1 "
                "AND (end_date IS NULL OR end_date >= CURRENT_DATE)",
                (user_id,),
            )
            for cname, end in cur.fetchall():
                if cname and str(cname).strip() not in out:
                    out[str(cname).strip()] = {"course_name": str(cname).strip(),
                                               "end_date": end, "source": "p"}
    return list(out.values())


def db_user_has_active_sub_for_group(user_id: int, group_id: int) -> bool:
    """
    True if this user holds an active (non-expired) subscription whose course
    maps to the given group id. Used to approve/deny Telegram join requests.
    """
    courses = db_get_courses_by_group_id(group_id)
    if not courses:
        return False
    names = {c["course_name"].strip().lower() for c in courses if c.get("course_name")}
    for sub in db_get_active_subs_by_userid(user_id):
        if sub["course_name"].strip().lower() in names:
            return True
    return False


# ─────────────────────────────────────────────
#  GUIDs
# ─────────────────────────────────────────────

def db_get_claimed_link(guid: str) -> Optional[dict]:
    """
    Return {'claimed_userid', 'claimed_date'} for a UniqueId that has already
    been claimed, or None if it has never been used (Approach A: trust-on-first-use).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT claimed_userid, claimed_date FROM guids WHERE guid=%s LIMIT 1",
                (guid,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {"claimed_userid": int(row[0]) if row[0] is not None else None,
            "claimed_date": str(row[1]) if row[1] is not None else None}


def db_claim_link(guid: str, userid: int):
    """Bind a UniqueId to the Telegram user who claimed it (idempotent)."""
    today = str(date.today())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO guids (guid, is_used, claimed_userid, claimed_date) "
                "VALUES (%s, 1, %s, %s) "
                "ON CONFLICT (guid) DO UPDATE SET is_used=1, "
                "claimed_userid=EXCLUDED.claimed_userid, claimed_date=EXCLUDED.claimed_date",
                (guid, userid, today),
            )


def db_get_guid_stats() -> dict:
    """Return {'claimed': int} — how many UniqueIds have been used."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM guids WHERE claimed_userid IS NOT NULL")
            claimed = cur.fetchone()[0]
    return {"claimed": claimed}


# ─────────────────────────────────────────────
#  Pending Invites
# ─────────────────────────────────────────────

def db_save_pending_invite(
    invite_link: str,
    link_name: str,
    course_name: str,
    months: int,
    start_date: str,
    end_date: str,
):
    today = str(date.today())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pending_invites "
                "(invite_link, link_name, course_name, months, start_date, end_date, created_date, is_used) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 0)",
                (invite_link, link_name, course_name, months, start_date, end_date, today),
            )


def db_get_pending_invite(invite_link: str) -> Optional[dict]:
    """Return pending invite data dict or None if not found / already used."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT course_name, months, start_date, end_date FROM pending_invites "
                "WHERE invite_link=%s AND is_used=0 LIMIT 1",
                (invite_link,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "course_name": str(row[0]),
        "months": int(row[1]),
        "start_date": str(row[2]),
        "end_date": str(row[3]),
    }


def db_mark_pending_invite_used(invite_link: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pending_invites SET is_used=1 WHERE invite_link=%s", (invite_link,)
            )


def db_get_all_pending_invites() -> list[dict]:
    cols = ["id", "invite_link", "link_name", "course_name", "months",
            "start_date", "end_date", "created_date", "is_used"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, invite_link, link_name, course_name, months, "
                "start_date, end_date, created_date, is_used "
                "FROM pending_invites ORDER BY id DESC"
            )
            rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


# ─────────────────────────────────────────────
#  Registered Users
# ─────────────────────────────────────────────

def db_save_registered_user(
    userid: int,
    username: str,
    invite_link_id: str,
    invite_link_url: str,
    registration_date: str,
    end_date: str,
    plan_type: str,
    link_used: int = 0,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO registered_users "
                "(userid, username, invite_link_id, invite_link_url, "
                "registration_date, end_date, plan_type, link_used, isremoved) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)",
                (userid, username, invite_link_id, invite_link_url,
                 registration_date, end_date, plan_type, link_used),
            )


def db_get_all_registered_users() -> list[dict]:
    cols = ["id", "userid", "username", "invite_link_id", "invite_link_url",
            "registration_date", "end_date", "plan_type", "link_used", "isremoved", "removed_date"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, userid, username, invite_link_id, invite_link_url, "
                "registration_date, end_date, plan_type, link_used, isremoved, removed_date "
                "FROM registered_users ORDER BY id"
            )
            rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def db_get_active_registered_users() -> list[dict]:
    """Return all users where isremoved=0."""
    cols = ["id", "userid", "username", "end_date", "plan_type"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, userid, username, end_date, plan_type "
                "FROM registered_users WHERE isremoved=0 ORDER BY id"
            )
            rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def db_remove_registered_user(user_id: int, removed_date: str):
    """Mark a registered user as removed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE registered_users SET isremoved=1, removed_date=%s "
                "WHERE userid=%s AND isremoved=0",
                (removed_date, user_id),
            )


def db_extend_registered_user(user_id: int, new_end_date: str):
    """Push out the expiry date of an active registered user (used by 'Save')."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE registered_users SET end_date=%s "
                "WHERE userid=%s AND isremoved=0",
                (new_end_date, user_id),
            )


def db_set_registered_invite_link(user_id: int, url: str):
    """Persist the single invite link issued for this user's active subscription.

    The deep link mints a member_limit=1 link only ONCE; every re-click reuses
    this stored value, so a UniqueId can never be used to generate extra links
    to share around. Returns nothing.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE registered_users SET invite_link_url=%s "
                "WHERE userid=%s AND isremoved=0",
                (url, user_id),
            )


def db_edit_registered_user(user_id: int, username: str, plan_type: str, end_date: str):
    """Edit an active registered user's editable fields (not tg id / join date)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE registered_users SET username=%s, plan_type=%s, end_date=%s "
                "WHERE userid=%s AND isremoved=0",
                (username, plan_type, end_date, user_id),
            )


def db_get_user_by_userid(user_id: int) -> Optional[dict]:
    """Return the most recent active registered_user row for this user_id, or None."""
    cols = ["id", "userid", "username", "end_date", "plan_type", "isremoved",
            "registration_date", "invite_link_url"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, userid, username, end_date, plan_type, isremoved, "
                "registration_date, invite_link_url "
                "FROM registered_users WHERE userid=%s ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return dict(zip(cols, row))


def db_get_stats() -> dict:
    """Return dashboard-level stats."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM registered_users WHERE isremoved=0")
            active_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM registered_users")
            total_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM courses")
            total_courses = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM guids WHERE claimed_userid IS NOT NULL")
            claimed_links = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM paid_users WHERE is_active=1")
            active_paid = cur.fetchone()[0]
    return {
        "active_users": active_users,
        "total_users": total_users,
        "total_courses": total_courses,
        "claimed_links": claimed_links,
        "active_paid_users": active_paid,
    }


# ─────────────────────────────────────────────
#  Paid Users
# ─────────────────────────────────────────────

def db_add_paid_user(
    user_id: int,
    username: str,
    course: str,
    start_date: str,
    end_date: str,
):
    today = str(date.today())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO paid_users (user_id, username, course, start_date, end_date, added_date, is_active) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1)",
                (user_id, username, course, start_date, end_date, today),
            )


def db_remove_paid_user(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE paid_users SET is_active=0 WHERE user_id=%s", (user_id,)
            )


def db_edit_paid_user(user_id: int, course: str, start_date: str, end_date: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE paid_users SET course=%s, start_date=%s, end_date=%s WHERE user_id=%s",
                (course, start_date, end_date, user_id),
            )


def db_extend_paid_user(user_id: int, new_end_date: str):
    """Push out the expiry date of an active paid user (used by 'Save')."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE paid_users SET end_date=%s WHERE user_id=%s AND is_active=1",
                (new_end_date, user_id),
            )


def db_get_paid_user(user_id: int) -> Optional[dict]:
    cols = ["id", "user_id", "username", "course", "start_date", "end_date", "added_date", "is_active"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, username, course, start_date, end_date, added_date, is_active "
                "FROM paid_users WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return dict(zip(cols, row))


def db_get_all_paid_users() -> list[dict]:
    cols = ["id", "user_id", "username", "course", "start_date", "end_date", "added_date", "is_active"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, username, course, start_date, end_date, added_date, is_active "
                "FROM paid_users ORDER BY id"
            )
            rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


# ─────────────────────────────────────────────
#  Invite Links
# ─────────────────────────────────────────────

def db_save_invite_link(link_id: str, user_id: int, invite_link: str):
    today = str(date.today())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO invite_links (link_id, user_id, invite_link, created_date, is_revoked) "
                "VALUES (%s, %s, %s, %s, 0)",
                (link_id, user_id, invite_link, today),
            )


# ─────────────────────────────────────────────
#  Admin Panel Users
# ─────────────────────────────────────────────

def db_get_admin_user(username: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash, role, tg_id "
                "FROM admin_panel_users WHERE username=%s",
                (username,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2],
            "role": row[3], "tg_id": row[4]}


def db_create_admin_user(username: str, password_hash: str, role: str = "admin",
                         tg_id: Optional[int] = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO admin_panel_users (username, password_hash, role, tg_id) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (username) DO NOTHING",
                (username, password_hash, role, tg_id),
            )


def db_get_all_admin_users() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, role, tg_id, created_at FROM admin_panel_users ORDER BY id"
            )
            rows = cur.fetchall()
    return [{"id": r[0], "username": r[1], "role": r[2], "tg_id": r[3],
             "created_at": str(r[4])} for r in rows]


def db_remove_admin_user(username: str) -> bool:
    """Remove a panel account. The owner account can never be deleted."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM admin_panel_users WHERE username=%s AND role <> 'owner'",
                (username,),
            )
            return cur.rowcount > 0


def db_change_admin_password(username: str, new_hash: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE admin_panel_users SET password_hash=%s WHERE username=%s",
                (new_hash, username),
            )


def db_set_admin_role(username: str, role: str):
    """Force an account's role (used to keep the env owner as 'owner')."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE admin_panel_users SET role=%s WHERE username=%s",
                (role, username),
            )


def db_set_admin_tgid(username: str, tg_id: Optional[int]):
    """Set/refresh the Telegram id stored on a panel account."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE admin_panel_users SET tg_id=%s WHERE username=%s",
                (tg_id, username),
            )


def db_is_panel_member(tg_id: int) -> bool:
    """True if this Telegram id belongs to any panel account (owner/superadmin/admin)."""
    if tg_id is None:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM admin_panel_users WHERE tg_id=%s LIMIT 1", (tg_id,))
            return cur.fetchone() is not None


def db_update_admin_username(old_username: str, new_username: str) -> bool:
    """Change a panel account's login email. Returns False on a name clash."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM admin_panel_users WHERE username=%s", (new_username,))
            if cur.fetchone():
                return False
            cur.execute(
                "UPDATE admin_panel_users SET username=%s WHERE username=%s",
                (new_username, old_username),
            )
            return cur.rowcount > 0


# ─────────────────────────────────────────────
#  Bot Settings
# ─────────────────────────────────────────────

def db_get_all_settings() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value, label FROM bot_settings ORDER BY key")
            rows = cur.fetchall()
    return [{"key": r[0], "value": str(r[1] or ""), "label": str(r[2] or r[0])} for r in rows]


def db_get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key=%s", (key,))
            row = cur.fetchone()
    return str(row[0]) if row else default


def db_set_setting(key: str, value: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value=%s, updated_at=NOW()",
                (key, value, value),
            )


# ─────────────────────────────────────────────
#  Audit Logs
# ─────────────────────────────────────────────

def db_add_log(actor: str, action: str, detail: str = ""):
    """Best-effort audit log — never raise into the caller."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_logs (actor, action, detail) VALUES (%s, %s, %s)",
                    (actor or "", action, detail or ""),
                )
    except Exception as e:
        print(f"[LOG] could not write audit log: {e}")


def db_get_logs(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ts, actor, action, detail FROM audit_logs "
                "ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [{"id": r[0], "ts": str(r[1]), "actor": r[2], "action": r[3], "detail": r[4]} for r in rows]
