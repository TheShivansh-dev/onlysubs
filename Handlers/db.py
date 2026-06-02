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

def db_load_courses() -> pd.DataFrame:
    """Return courses table as a DataFrame with columns ['id', 'course_name']."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, course_name FROM courses ORDER BY id")
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=["id", "course_name"])
    return pd.DataFrame(rows, columns=["id", "course_name"])


def db_save_course(course_name: str):
    """Insert a new course."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO courses (course_name) VALUES (%s)", (course_name,))


def db_delete_course(course_id: int):
    """Delete a course by id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM courses WHERE id=%s", (course_id,))


# ─────────────────────────────────────────────
#  GUIDs
# ─────────────────────────────────────────────

def db_generate_guids(count: int = 100) -> int:
    """Generate `count` new GUIDs and insert into DB. Returns actual count inserted."""
    rows = [(uuid.uuid4().hex[:8],) for _ in range(count)]
    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for (g,) in rows:
                try:
                    cur.execute("INSERT INTO guids (guid, is_used) VALUES (%s, 0)", (g,))
                    inserted += 1
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
    return inserted


def db_verify_guid(guid: str) -> str:
    """
    Returns 'ok' (unused), 'used' (already used), or 'not_found'.
    Import GUID_OK / GUID_USED / GUID_NOT_FOUND from config for comparison.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_used FROM guids WHERE guid=%s", (guid,))
            row = cur.fetchone()
    if row is None:
        return "not_found"
    return "ok" if int(row[0]) == 0 else "used"


def db_mark_guid_used(guid: str):
    """Mark a GUID as used."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE guids SET is_used=1 WHERE guid=%s", (guid,))


def db_get_guid_samples(n: int = 3) -> list[str]:
    """Return up to n unused GUID strings (most recently inserted)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT guid FROM guids WHERE is_used=0 ORDER BY id DESC LIMIT %s", (n,)
            )
            rows = cur.fetchall()
    return [r[0] for r in rows]


def db_get_guid_stats() -> dict:
    """Return {'available': int, 'used': int, 'total': int}."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM guids WHERE is_used=0")
            available = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM guids WHERE is_used=1")
            used = cur.fetchone()[0]
    return {"available": available, "used": used, "total": available + used}


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
            cur.execute("SELECT COUNT(*) FROM guids WHERE is_used=0")
            available_guids = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM paid_users WHERE is_active=1")
            active_paid = cur.fetchone()[0]
    return {
        "active_users": active_users,
        "total_users": total_users,
        "total_courses": total_courses,
        "available_guids": available_guids,
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
                "SELECT id, username, password_hash, role FROM admin_panel_users WHERE username=%s",
                (username,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "role": row[3]}


def db_create_admin_user(username: str, password_hash: str, role: str = "admin"):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO admin_panel_users (username, password_hash, role) VALUES (%s, %s, %s) "
                "ON CONFLICT (username) DO NOTHING",
                (username, password_hash, role),
            )


def db_get_all_admin_users() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, role, created_at FROM admin_panel_users ORDER BY id"
            )
            rows = cur.fetchall()
    return [{"id": r[0], "username": r[1], "role": r[2], "created_at": str(r[3])} for r in rows]


def db_remove_admin_user(username: str) -> bool:
    """Remove an admin — superadmin accounts cannot be deleted."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM admin_panel_users WHERE username=%s AND role='admin'",
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
