"""
web/main.py
FastAPI admin panel API.
Run with: uvicorn web.main:app --host 0.0.0.0 --port $PORT
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from jose import JWTError, jwt

# ── DB imports ──────────────────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from Handlers.auth import hash_password, verify_password
from Handlers.db import (
    init_schema,
    db_get_admin_user,
    db_create_admin_user,
    db_get_all_admin_users,
    db_remove_admin_user,
    db_change_admin_password,
    db_update_admin_username,
    db_set_admin_role,
    db_set_admin_tgid,
    db_get_stats,
    db_get_stats_for_courses,
    db_load_courses,
    db_save_course,
    db_update_course,
    db_delete_course,
    db_get_courses_for_admin,
    db_get_admin_course_names,
    db_get_all_registered_users,
    db_get_user_by_userid,
    db_remove_registered_user,
    db_edit_registered_user,
    db_reactivate_registered_user,
    db_get_all_paid_users,
    db_get_paid_user,
    db_add_paid_user,
    db_remove_paid_user,
    db_edit_paid_user,
    db_get_all_settings,
    db_set_setting,
    db_get_setting,
    db_get_current_admin_group,
    db_set_admin_group,
    db_remove_admin_group,
    db_add_log,
    db_get_logs,
    db_get_owner_usernames,
)
from Handlers.config import make_course_code

# ── Config ───────────────────────────────────────────────────────────────────
SECRET_KEY    = os.environ.get("JWT_SECRET", "change-me-in-production-jwt-secret-key-32chars")
ALGORITHM     = "HS256"
TOKEN_EXPIRE  = 8  # hours

# Top-level OWNER account is seeded from env only (no secrets in code).
OWNER_EMAIL    = os.environ.get("OWNER_EMAIL", "")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")
_owner_tg_raw  = os.environ.get("OWNER_TG_ID", "").strip()
OWNER_TG_ID    = int(_owner_tg_raw) if _owner_tg_raw.lstrip("-").isdigit() else None

# Role hierarchy: owner > superadmin > admin
ROLE_RANK = {"admin": 1, "superadmin": 2, "owner": 3}

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/login")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="OnlySubscriber Admin", docs_url=None, redoc_url=None)


# Holds the running Telegram bot when RUN_BOT is enabled (single-service mode).
_bot_app = None


@app.on_event("startup")
async def startup():
    init_schema()
    # Seed the owner account from env, only if both vars are set.
    if OWNER_EMAIL and OWNER_PASSWORD:
        existing = db_get_admin_user(OWNER_EMAIL)
        if not existing:
            db_create_admin_user(OWNER_EMAIL, hash_password(OWNER_PASSWORD),
                                 role="owner", tg_id=OWNER_TG_ID)
        else:
            if existing.get("role") != "owner":
                # Account already existed with a lower role — promote it to owner.
                db_set_admin_role(OWNER_EMAIL, "owner")
            if OWNER_TG_ID is not None and existing.get("tg_id") != OWNER_TG_ID:
                db_set_admin_tgid(OWNER_EMAIL, OWNER_TG_ID)
    else:
        print("[STARTUP] OWNER_EMAIL / OWNER_PASSWORD not set — owner account not seeded.")

    # Run the Telegram bot inside this web process (free single-service mode).
    # Disable by setting RUN_BOT=0 when the bot runs as its own worker.
    if os.environ.get("RUN_BOT", "1") != "0":
        global _bot_app
        try:
            from bot import build_application, ALLOWED_UPDATES
            _bot_app = build_application()
            await _bot_app.initialize()
            await _bot_app.start()
            await _bot_app.updater.start_polling(allowed_updates=ALLOWED_UPDATES)
            print("[STARTUP] Telegram bot polling started (single-service mode).")
        except Exception as e:
            print(f"[STARTUP] Could not start Telegram bot: {e}")


@app.on_event("shutdown")
async def shutdown():
    global _bot_app
    if _bot_app is not None:
        try:
            await _bot_app.updater.stop()
            await _bot_app.stop()
            await _bot_app.shutdown()
        except Exception as e:
            print(f"[SHUTDOWN] Error stopping bot: {e}")
        _bot_app = None


# ── Auth helpers ─────────────────────────────────────────────────────────────

def _verify_password(plain: str, hashed: str) -> bool:
    return verify_password(plain, hashed)


def _create_token(username: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE)
    return jwt.encode({"sub": username, "role": role, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def _get_current_user(token: str = Depends(oauth2)) -> dict:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str     = payload.get("role", "admin")
        if not username:
            raise credentials_exc
    except JWTError:
        raise credentials_exc
    user = db_get_admin_user(username)
    if not user:
        raise credentials_exc
    # Trust the DB row over the token (role + canonical-case username).
    return {"username": user["username"], "role": user["role"]}


def _require_manager(current: dict = Depends(_get_current_user)) -> dict:
    """Owner or superadmin — may manage accounts, settings and the admin group."""
    if current["role"] not in ("owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Manager access required")
    return current


def _require_owner(current: dict = Depends(_get_current_user)) -> dict:
    if current["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return current


def _diff(fields) -> str:
    """Build a 'before → after' summary for the audit log from
    (label, old, new) tuples, listing only the fields that actually changed."""
    parts = []
    for label, old, new in fields:
        old_s = "" if old is None else str(old)
        new_s = "" if new is None else str(new)
        if old_s != new_s:
            parts.append(f"{label}: {old_s or '∅'} → {new_s or '∅'}")
    return "; ".join(parts) if parts else "no change"


def _allowed_courses(user: dict):
    """
    Course-scoping for a plain admin: returns the set of course names they are
    assigned to. Returns None for owner/superadmin, meaning no restriction.
    """
    if user["role"] == "admin":
        return db_get_admin_course_names(user["username"])
    return None


def _assert_course_allowed(user: dict, course_name: str):
    """Block a scoped admin from touching a course outside their assignment."""
    allowed = _allowed_courses(user)
    if allowed is not None and (course_name or "") not in allowed:
        raise HTTPException(403, "This course is not assigned to you")


# ── Static files & SPA ───────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def serve_spa():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ── Auth endpoint ─────────────────────────────────────────────────────────────

@app.post("/api/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    user = db_get_admin_user(form.username)
    if not user or not _verify_password(form.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    # Use the canonical stored username so case typed at login doesn't matter.
    token = _create_token(user["username"], user["role"])
    db_add_log(user["username"], "login", user["role"])
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}


# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
def get_stats(current_user: dict = Depends(_get_current_user)):
    # A scoped admin sees counts only for their assigned course(s).
    allowed = _allowed_courses(current_user)
    if allowed is not None:
        return db_get_stats_for_courses(allowed)
    return db_get_stats()


# ── Courses ───────────────────────────────────────────────────────────────────
@app.get("/api/courses")
def list_courses(current_user: dict = Depends(_get_current_user)):
    # A scoped admin only sees the course(s) assigned to them.
    if current_user["role"] == "admin":
        return db_get_courses_for_admin(current_user["username"])
    df = db_load_courses()
    return df.to_dict(orient="records")


class CourseBody(BaseModel):
    course_name: str
    group_link: Optional[str] = ""
    group_id: Optional[int] = None
    assigned_admins: Optional[list] = None   # admin login emails managing this course
    website_url: Optional[str] = ""          # optional course/renewal website link


@app.post("/api/courses", status_code=201)
def add_course(body: CourseBody, current_user: dict = Depends(_get_current_user)):
    name = body.course_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="course_name is required")

    # Who manages the course:
    #  - a plain admin can only create courses for themselves;
    #  - a manager may assign it to any number of existing admin accounts.
    if current_user["role"] == "admin":
        assigned_list = [current_user["username"]]
    else:
        assigned_list = []
        for a in (body.assigned_admins or []):
            a = (a or "").strip()
            if not a:
                continue
            target = db_get_admin_user(a)
            if not target or target["role"] != "admin":
                raise HTTPException(400, f"'{a}' is not an existing admin account")
            if target["username"] not in assigned_list:   # canonical, deduped
                assigned_list.append(target["username"])

    assigned = ",".join(assigned_list) if assigned_list else None
    db_save_course(name, make_course_code(name), (body.group_link or "").strip(),
                   body.group_id, assigned_admin=assigned,
                   website_url=((body.website_url or "").strip() or None))
    db_add_log(current_user["username"], "course_add",
               f"{name}" + (f" → admins: {assigned}" if assigned else ""))
    return {"ok": True, "course_code": make_course_code(name)}


@app.put("/api/courses/{course_id}")
def update_course(course_id: int, body: CourseBody, current_user: dict = Depends(_get_current_user)):
    # Name and code are NOT editable here — only the group, website and admins.
    if current_user["role"] == "admin":
        # Scoped admin: may edit only their own course; cannot change assignees.
        mine = {c["id"] for c in db_get_courses_for_admin(current_user["username"])}
        if course_id not in mine:
            raise HTTPException(403, "This course is not assigned to you")
        assigned = None   # keep existing assignment
    else:
        # Manager: sets the full assignee list (empty list clears all admins).
        assigned_list = []
        for a in (body.assigned_admins or []):
            a = (a or "").strip()
            if not a:
                continue
            target = db_get_admin_user(a)
            if not target or target["role"] != "admin":
                raise HTTPException(400, f"'{a}' is not an existing admin account")
            if target["username"] not in assigned_list:
                assigned_list.append(target["username"])
        assigned = ",".join(assigned_list)   # "" clears, never None for a manager

    ok = db_update_course(course_id, (body.group_link or "").strip(), body.group_id,
                          website_url=(body.website_url or "").strip(),
                          assigned_admin=assigned)
    if not ok:
        raise HTTPException(404, "Course not found")
    db_add_log(current_user["username"], "course_edit", f"id={course_id}")
    return {"ok": True}


@app.delete("/api/courses/{course_id}")
def delete_course(course_id: int, current_user: dict = Depends(_get_current_user)):
    # A scoped admin may only deactivate a course assigned to them.
    if current_user["role"] == "admin":
        mine = {c["id"] for c in db_get_courses_for_admin(current_user["username"])}
        if course_id not in mine:
            raise HTTPException(403, "This course is not assigned to you")
    db_delete_course(course_id)
    db_add_log(current_user["username"], "course_deactivate", f"id={course_id}")
    return {"ok": True}


# ── Registered Users ──────────────────────────────────────────────────────────

@app.get("/api/users")
def list_users(current_user: dict = Depends(_get_current_user)):
    users = db_get_all_registered_users()
    allowed = _allowed_courses(current_user)
    if allowed is not None:
        users = [u for u in users if (u.get("plan_type") or "") in allowed]
    # Serialise date objects to strings
    for u in users:
        for k, v in u.items():
            if hasattr(v, "isoformat"):
                u[k] = v.isoformat()
    return users


async def _kick_from_group(kind: str, user_id: int) -> bool:
    """
    Kick a user out of their course group via the running bot, reusing the same
    kick_user() the daily expiry job uses (ban then immediate unban — never left
    banned). On failure kick_user writes the exact Telegram reason to the Logs.
    """
    if _bot_app is None:
        return False
    from Handlers.subscription import resolve_group_id, kick_user
    group_id = resolve_group_id(kind, user_id)
    if not group_id:
        return False
    return await kick_user(_RunCtx(_bot_app.bot), group_id, user_id)


@app.delete("/api/users/{user_id}")
async def remove_user(user_id: int, current_user: dict = Depends(_get_current_user)):
    row = db_get_user_by_userid(user_id) or {}
    _assert_course_allowed(current_user, row.get("plan_type") or "")
    kicked = await _kick_from_group("r", user_id)
    today = str(datetime.utcnow().date())
    db_remove_registered_user(user_id, today)
    db_add_log(current_user["username"], "user_remove",
               f"userid={user_id} kicked={kicked}")
    return {"ok": True, "kicked": kicked}


class EditUserBody(BaseModel):
    username: Optional[str] = ""
    plan_type: str                   # course name
    end_date: str                    # YYYY-MM-DD


@app.put("/api/users/{user_id}")
def edit_user(user_id: int, body: EditUserBody, current_user: dict = Depends(_get_current_user)):
    before = db_get_user_by_userid(user_id) or {}
    new_name   = (body.username or "").strip()
    new_course = body.plan_type.strip()
    new_end    = body.end_date.strip()
    # A scoped admin may only touch (and move within) their own course(s).
    _assert_course_allowed(current_user, before.get("plan_type") or "")
    _assert_course_allowed(current_user, new_course)
    db_edit_registered_user(user_id, new_name, new_course, new_end)
    diff = _diff([
        ("name",   before.get("username"),  new_name),
        ("course", before.get("plan_type"), new_course),
        ("ends",   before.get("end_date"),  new_end),
    ])
    db_add_log(current_user["username"], "user_edit", f"userid={user_id} — {diff}")
    return {"ok": True}


@app.put("/api/users/{user_id}/activate")
def activate_user(user_id: int, current_user: dict = Depends(_get_current_user)):
    """Re-activate a removed subscriber whose date is still valid (not expired)."""
    row = db_get_user_by_userid(user_id) or {}
    _assert_course_allowed(current_user, row.get("plan_type") or "")
    ok = db_reactivate_registered_user(user_id)
    if not ok:
        raise HTTPException(400, "No re-activatable subscription (expired or not found)")
    db_add_log(current_user["username"], "user_activate", f"userid={user_id}")
    return {"ok": True}


# ── Paid Users ────────────────────────────────────────────────────────────────

@app.get("/api/paid-users")
def list_paid_users(current_user: dict = Depends(_get_current_user)):
    users = db_get_all_paid_users()
    allowed = _allowed_courses(current_user)
    if allowed is not None:
        users = [u for u in users if (u.get("course") or "") in allowed]
    for u in users:
        for k, v in u.items():
            if hasattr(v, "isoformat"):
                u[k] = v.isoformat()
    return users


class PaidUserBody(BaseModel):
    user_id: int
    username: Optional[str] = ""
    course: str                      # course name
    months: int
    group_link: Optional[str] = ""
    group_id: Optional[int] = None


@app.post("/api/paid-users", status_code=201)
def add_paid_user(body: PaidUserBody, current_user: dict = Depends(_get_current_user)):
    if body.months < 1:
        raise HTTPException(status_code=400, detail="months must be >= 1")
    course_name = body.course.strip()
    if not course_name:
        raise HTTPException(status_code=400, detail="course is required")
    _assert_course_allowed(current_user, course_name)

    today    = datetime.utcnow().date()
    end_date = today + timedelta(days=30 * body.months)

    # Keep the course → group mapping in sync so expiry/removal can find the group.
    if (body.group_link or "").strip() or body.group_id is not None:
        db_save_course(course_name, make_course_code(course_name),
                       (body.group_link or "").strip(), body.group_id)

    db_add_paid_user(
        body.user_id,
        body.username or f"user_{body.user_id}",
        course_name,
        str(today),
        str(end_date),
    )
    db_add_log(current_user["username"], "paid_user_add", f"userid={body.user_id} course={course_name} months={body.months}")
    return {"ok": True, "end_date": str(end_date)}


@app.delete("/api/paid-users/{user_id}")
def remove_paid_user(user_id: int, current_user: dict = Depends(_get_current_user)):
    before = db_get_paid_user(user_id) or {}
    _assert_course_allowed(current_user, before.get("course") or "")
    db_remove_paid_user(user_id)
    db_add_log(current_user["username"], "paid_user_remove", f"userid={user_id}")
    return {"ok": True}


class EditPaidUserBody(BaseModel):
    course: str
    start_date: str
    end_date: str


@app.put("/api/paid-users/{user_id}")
def edit_paid_user(user_id: int, body: EditPaidUserBody, current_user: dict = Depends(_get_current_user)):
    before = db_get_paid_user(user_id) or {}
    _assert_course_allowed(current_user, before.get("course") or "")
    _assert_course_allowed(current_user, body.course.strip())
    db_edit_paid_user(user_id, body.course, body.start_date, body.end_date)
    diff = _diff([
        ("course", before.get("course"),     body.course),
        ("start",  before.get("start_date"), body.start_date),
        ("ends",   before.get("end_date"),   body.end_date),
    ])
    db_add_log(current_user["username"], "paid_user_edit", f"userid={user_id} — {diff}")
    return {"ok": True}


# ── Admin Group  (SuperAdmin only) ────────────────────────────────────────────

@app.get("/api/admin-group")
def get_admin_group(sa: dict = Depends(_require_manager)):
    """Return the single active admin group, or null."""
    return db_get_current_admin_group()


class AdminGroupBody(BaseModel):
    group_id: int
    group_name: Optional[str] = ""


@app.put("/api/admin-group")
def set_admin_group(body: AdminGroupBody, sa: dict = Depends(_require_manager)):
    db_set_admin_group(body.group_id, (body.group_name or "").strip())
    db_add_log(sa["username"], "admin_group_set", str(body.group_id))
    return {"ok": True}


@app.delete("/api/admin-group")
def clear_admin_group(sa: dict = Depends(_require_manager)):
    removed = db_remove_admin_group()
    db_add_log(sa["username"], "admin_group_clear", "")
    return {"ok": True, "removed": removed}


# ── Account Management ────────────────────────────────────────────────────────
#  owner  → can manage superadmin + admin accounts
#  superadmin → can manage admin accounts only
#  Every account can edit its own profile (email + password) via /api/me.

# Which roles a manager may create / manage, by their own role.
_MANAGEABLE = {
    "owner":      {"superadmin", "admin"},
    "superadmin": {"admin"},
}


@app.get("/api/admins")
def list_admins(sa: dict = Depends(_require_manager)):
    """Owner sees superadmin+admin; superadmin sees admin only. Owner rows are never listed."""
    allowed = _MANAGEABLE.get(sa["role"], set())
    return [a for a in db_get_all_admin_users() if a["role"] in allowed]


class AddAdminBody(BaseModel):
    username: str            # login email
    password: str
    role: str = "admin"
    tg_id: Optional[int] = None


@app.post("/api/admins", status_code=201)
def add_admin(body: AddAdminBody, sa: dict = Depends(_require_manager)):
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(400, "email and password required")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    allowed = _MANAGEABLE.get(sa["role"], set())
    if body.role not in allowed:
        raise HTTPException(403, f"You may only create: {', '.join(sorted(allowed)) or 'none'}")
    if db_get_admin_user(username):
        raise HTTPException(400, "An account with this email already exists")
    db_create_admin_user(username, hash_password(body.password), role=body.role, tg_id=body.tg_id)
    db_add_log(sa["username"], "account_add", f"{username} ({body.role})")
    return {"ok": True}


def _assert_can_manage(actor: dict, target_username: str) -> dict:
    """Ensure actor outranks the target account; return the target row."""
    target = db_get_admin_user(target_username)
    if not target:
        raise HTTPException(404, "Account not found")
    if target["role"] not in _MANAGEABLE.get(actor["role"], set()):
        raise HTTPException(403, "You cannot manage this account")
    return target


@app.delete("/api/admins/{username}")
def remove_admin(username: str, sa: dict = Depends(_require_manager)):
    if username.lower() == sa["username"].lower():
        raise HTTPException(400, "Cannot remove yourself")
    _assert_can_manage(sa, username)
    removed = db_remove_admin_user(username)
    if not removed:
        raise HTTPException(404, "Account not found or is the owner")
    db_add_log(sa["username"], "account_remove", username)
    return {"ok": True}


class ChangePasswordBody(BaseModel):
    new_password: str


@app.put("/api/admins/{username}/password")
def change_password(username: str, body: ChangePasswordBody,
                    sa: dict = Depends(_require_manager)):
    _assert_can_manage(sa, username)
    if not body.new_password or len(body.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    db_change_admin_password(username, hash_password(body.new_password))
    db_add_log(sa["username"], "account_password_reset", username)
    return {"ok": True}


# ── Account editing by a manager (email / password / role) ────────────────────
#  A manager edits accounts BELOW them (owner → superadmin+admin,
#  superadmin → admin). Nobody can edit their OWN account here (and so nobody can
#  change their own email). Role changes are owner-only.

class EditAccountBody(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None
    new_password: Optional[str] = None


@app.put("/api/accounts/{username}")
def edit_account(username: str, body: EditAccountBody,
                 sa: dict = Depends(_require_manager)):
    if username.lower() == sa["username"].lower():
        raise HTTPException(403, "You cannot edit your own account here")
    target = _assert_can_manage(sa, username)   # enforces the role hierarchy

    changes = []
    # Role change is owner-only.
    if body.role and body.role in ("admin", "superadmin") and body.role != target["role"]:
        if sa["role"] != "owner":
            raise HTTPException(403, "Only the owner can change roles")
        db_set_admin_role(username, body.role)
        changes.append(f"role: {target['role']} → {body.role}")
    if body.new_password:
        if len(body.new_password) < 6:
            raise HTTPException(400, "Password must be at least 6 characters")
        db_change_admin_password(username, hash_password(body.new_password))
        changes.append("password changed")
    final_name = username
    if body.email and body.email.strip() and body.email.strip() != username:
        new_email = body.email.strip()
        if "@" not in new_email:
            raise HTTPException(400, "A valid email is required")
        ok = db_update_admin_username(username, new_email)
        if not ok:
            raise HTTPException(400, "That email is already in use")
        final_name = new_email
        changes.append(f"email: {username} → {new_email}")

    db_add_log(sa["username"], "account_edit", f"{username} — {'; '.join(changes) or 'no change'}")
    return {"ok": True, "username": final_name}


# ── Expiry scheduler  (owner only — testing/maintenance) ──────────────────────
#  Owner can change the daily run time of the expiry check, and trigger a run
#  immediately to test reminders / admin-group cards / auto-removal.

class _RunCtx:
    """Minimal stand-in for a PTB job context — exposes only .bot."""
    def __init__(self, bot):
        self.bot = bot


@app.get("/api/owner/scheduler")
def get_scheduler(owner: dict = Depends(_require_owner)):
    from Handlers.db import db_get_setting
    from bot import DEFAULT_EXPIRY_TIME, EXPIRY_JOB_NAME
    hhmm = db_get_setting("EXPIRY_CHECK_TIME", DEFAULT_EXPIRY_TIME)
    next_run = None
    if _bot_app is not None and _bot_app.job_queue is not None:
        jobs = _bot_app.job_queue.get_jobs_by_name(EXPIRY_JOB_NAME)
        if jobs and jobs[0].next_t:
            next_run = jobs[0].next_t.isoformat()
    return {"time": hhmm, "next_run": next_run, "bot_running": _bot_app is not None}


class SchedulerBody(BaseModel):
    time: str    # "HH:MM" in IST


@app.put("/api/owner/scheduler")
def set_scheduler(body: SchedulerBody, owner: dict = Depends(_require_owner)):
    raw = (body.time or "").strip()
    parts = raw.split(":")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()) \
            or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
        raise HTTPException(400, "Time must be HH:MM (00:00–23:59), IST")
    hhmm = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    db_set_setting("EXPIRY_CHECK_TIME", hhmm)
    rescheduled = False
    if _bot_app is not None:
        from bot import reschedule_expiry_job
        reschedule_expiry_job(_bot_app, hhmm)
        rescheduled = True
    db_add_log(owner["username"], "scheduler_set", f"time={hhmm} (IST)")
    return {"ok": True, "time": hhmm, "rescheduled": rescheduled}


@app.post("/api/owner/run-expiry")
async def run_expiry_now(owner: dict = Depends(_require_owner)):
    if _bot_app is None:
        raise HTTPException(503, "Bot is not running in this process (set RUN_BOT=1).")
    from Handlers.subscription import check_subscription_expiry
    await check_subscription_expiry(_RunCtx(_bot_app.bot))
    db_add_log(owner["username"], "run_expiry", "manual trigger")
    return {"ok": True}


# ── Self profile  (any logged-in account) ─────────────────────────────────────

@app.get("/api/me")
def get_me(current: dict = Depends(_get_current_user)):
    user = db_get_admin_user(current["username"])
    return {"username": current["username"], "role": current["role"],
            "tg_id": user.get("tg_id") if user else None}


class MyPasswordBody(BaseModel):
    new_password: str


@app.put("/api/me/password")
def change_my_password(body: MyPasswordBody, current: dict = Depends(_get_current_user)):
    if not body.new_password or len(body.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    db_change_admin_password(current["username"], hash_password(body.new_password))
    return {"ok": True}


# Note: there is deliberately no self email-change endpoint — a login email can
# only be changed by a manager above the account (owner/superadmin), never by the
# account itself.


# ── Bot Settings  (SuperAdmin only) ───────────────────────────────────────────

# These either live elsewhere or are read from env — never editable here.
# ADMIN_GROUP_ID is managed by the "Admin Group" card (master_groups);
# ADMIN_USER_ID is no longer used (admin recognition = panel accounts' tg_id).
_HIDDEN_SETTINGS = {
    "BOT_CREATOR_USER_ID", "BOT_USERNAME", "BOT_CREATOR_GROUP_ID",
    "ADMIN_GROUP_ID", "ADMIN_USER_ID",
    # Managed via the owner-only Expiry Scheduler card, not the raw settings table.
    "EXPIRY_CHECK_TIME",
    # Internal bookkeeping for the every-2nd-day log wipe.
    "LAST_LOG_CLEAR",
}

@app.get("/api/settings")
def get_settings(sa: dict = Depends(_require_manager)):
    return [s for s in db_get_all_settings() if s["key"] not in _HIDDEN_SETTINGS]


class SettingBody(BaseModel):
    value: str


@app.put("/api/settings/{key}")
def update_setting(key: str, body: SettingBody, sa: dict = Depends(_require_manager)):
    if key in _HIDDEN_SETTINGS:
        raise HTTPException(403, "This setting cannot be changed from the panel")
    old = db_get_setting(key, "")
    new = body.value.strip()
    db_set_setting(key, new)
    db_add_log(sa["username"], "setting_update", f"{key} — {_diff([('value', old, new)])}")
    return {"ok": True}


# ── Manual reminders  (any logged-in account) ─────────────────────────────────
#  Sends a custom message (+ optional link button) to selected subscribers via
#  the bot running in this process.

class ReminderBody(BaseModel):
    user_ids: list[int]
    message: str
    link: Optional[str] = ""
    button_label: Optional[str] = "Open"


@app.post("/api/reminders/send")
async def send_reminders(body: ReminderBody, current: dict = Depends(_get_current_user)):
    if _bot_app is None:
        raise HTTPException(503, "Bot is not running in this process (set RUN_BOT=1).")
    if not body.user_ids:
        raise HTTPException(400, "Select at least one user")
    if not body.message.strip():
        raise HTTPException(400, "Message is required")

    # A scoped admin can only message subscribers in their own course(s).
    allowed = _allowed_courses(current)
    target_ids = body.user_ids
    if allowed is not None:
        mine = {u["userid"] for u in db_get_all_registered_users()
                if (u.get("plan_type") or "") in allowed}
        target_ids = [uid for uid in body.user_ids if uid in mine]
        if not target_ids:
            raise HTTPException(403, "None of the selected users are in your course(s)")

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = None
    if (body.link or "").strip():
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            (body.button_label or "Open").strip() or "Open", url=body.link.strip())]])

    sent = failed = 0
    for uid in target_ids:
        try:
            await _bot_app.bot.send_message(chat_id=uid, text=body.message, reply_markup=kb)
            sent += 1
        except Exception as e:
            failed += 1
            print(f"[REMINDER] failed for {uid}: {e}")

    db_add_log(current["username"], "reminder_send", f"sent={sent} failed={failed}")
    return {"sent": sent, "failed": failed}


# ── Audit logs  (managers) ────────────────────────────────────────────────────

@app.get("/api/logs")
def get_logs(sa: dict = Depends(_require_manager)):
    logs = db_get_logs(300)
    # Owner activity is private — superadmin/admin never see owner log entries.
    if sa["role"] != "owner":
        owners = set(db_get_owner_usernames())
        logs = [l for l in logs if l.get("actor") not in owners]
    return logs
