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
    db_get_stats,
    db_load_courses,
    db_save_course,
    db_delete_course,
    db_get_all_registered_users,
    db_remove_registered_user,
    db_get_all_paid_users,
    db_add_paid_user,
    db_remove_paid_user,
    db_edit_paid_user,
    db_get_guid_stats,
    db_get_all_groups,
    db_get_all_settings,
    db_set_setting,
    db_get_current_admin_group,
    db_set_admin_group,
    db_remove_admin_group,
)
from Handlers.config import make_course_code

# ── Config ───────────────────────────────────────────────────────────────────
SECRET_KEY    = os.environ.get("JWT_SECRET", "change-me-in-production-jwt-secret-key-32chars")
ALGORITHM     = "HS256"
TOKEN_EXPIRE  = 8  # hours

# Top-level OWNER account is seeded from env only (no secrets in code).
OWNER_EMAIL    = os.environ.get("OWNER_EMAIL", "")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")

# Role hierarchy: owner > superadmin > admin
ROLE_RANK = {"admin": 1, "superadmin": 2, "owner": 3}

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/login")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="OnlySubscriber Admin", docs_url=None, redoc_url=None)


@app.on_event("startup")
def startup():
    init_schema()
    # Seed the owner account from env, only if both vars are set.
    if OWNER_EMAIL and OWNER_PASSWORD:
        if not db_get_admin_user(OWNER_EMAIL):
            db_create_admin_user(OWNER_EMAIL, hash_password(OWNER_PASSWORD), role="owner")
    else:
        print("[STARTUP] OWNER_EMAIL / OWNER_PASSWORD not set — owner account not seeded.")


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
    # Trust the DB role over the (possibly stale) token role.
    return {"username": username, "role": user["role"]}


def _require_manager(current: dict = Depends(_get_current_user)) -> dict:
    """Owner or superadmin — may manage accounts, settings and the admin group."""
    if current["role"] not in ("owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Manager access required")
    return current


def _require_owner(current: dict = Depends(_get_current_user)) -> dict:
    if current["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return current


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
    token = _create_token(form.username, user["role"])
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats(current_user: dict = Depends(_get_current_user)):
    return db_get_stats()


# ── Courses ───────────────────────────────────────────────────────────────────

@app.get("/api/courses")
def list_courses(current_user: dict = Depends(_get_current_user)):
    df = db_load_courses()
    return df.to_dict(orient="records")


class CourseBody(BaseModel):
    course_name: str
    group_link: Optional[str] = ""
    group_id: Optional[int] = None


@app.post("/api/courses", status_code=201)
def add_course(body: CourseBody, current_user: dict = Depends(_require_manager)):
    name = body.course_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="course_name is required")
    db_save_course(name, make_course_code(name), (body.group_link or "").strip(), body.group_id)
    return {"ok": True, "course_code": make_course_code(name)}


@app.delete("/api/courses/{course_id}")
def delete_course(course_id: int, current_user: dict = Depends(_require_manager)):
    db_delete_course(course_id)
    return {"ok": True}


# ── Registered Users ──────────────────────────────────────────────────────────

@app.get("/api/users")
def list_users(current_user: dict = Depends(_get_current_user)):
    users = db_get_all_registered_users()
    # Serialise date objects to strings
    for u in users:
        for k, v in u.items():
            if hasattr(v, "isoformat"):
                u[k] = v.isoformat()
    return users


@app.delete("/api/users/{user_id}")
def remove_user(user_id: int, current_user: dict = Depends(_require_manager)):
    today = str(datetime.utcnow().date())
    db_remove_registered_user(user_id, today)
    return {"ok": True}


# ── Paid Users ────────────────────────────────────────────────────────────────

@app.get("/api/paid-users")
def list_paid_users(current_user: dict = Depends(_get_current_user)):
    users = db_get_all_paid_users()
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
def add_paid_user(body: PaidUserBody, current_user: dict = Depends(_require_manager)):
    if body.months < 1:
        raise HTTPException(status_code=400, detail="months must be >= 1")
    course_name = body.course.strip()
    if not course_name:
        raise HTTPException(status_code=400, detail="course is required")

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
    return {"ok": True, "end_date": str(end_date)}


@app.delete("/api/paid-users/{user_id}")
def remove_paid_user(user_id: int, current_user: dict = Depends(_require_manager)):
    db_remove_paid_user(user_id)
    return {"ok": True}


class EditPaidUserBody(BaseModel):
    course: str
    start_date: str
    end_date: str


@app.put("/api/paid-users/{user_id}")
def edit_paid_user(user_id: int, body: EditPaidUserBody, current_user: dict = Depends(_require_manager)):
    db_edit_paid_user(user_id, body.course, body.start_date, body.end_date)
    return {"ok": True}


# ── GUIDs ─────────────────────────────────────────────────────────────────────

@app.get("/api/guids")
def guid_stats(current_user: dict = Depends(_get_current_user)):
    """Returns {'claimed': N} — UniqueIds used so far (Approach A)."""
    return db_get_guid_stats()


# ── Groups ────────────────────────────────────────────────────────────────────

@app.get("/api/groups")
def list_groups(current_user: dict = Depends(_get_current_user)):
    groups = db_get_all_groups()
    for g in groups:
        for k, v in g.items():
            if hasattr(v, "isoformat"):
                g[k] = v.isoformat()
    return groups


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
    return {"ok": True}


@app.delete("/api/admin-group")
def clear_admin_group(sa: dict = Depends(_require_manager)):
    removed = db_remove_admin_group()
    return {"ok": True, "removed": removed}


# ── Pending Invites ───────────────────────────────────────────────────────────

@app.get("/api/pending-invites")
def list_pending_invites(current_user: dict = Depends(_get_current_user)):
    invites = db_get_all_pending_invites()
    for inv in invites:
        for k, v in inv.items():
            if hasattr(v, "isoformat"):
                inv[k] = v.isoformat()
    return invites


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
    if username == sa["username"]:
        raise HTTPException(400, "Cannot remove yourself")
    _assert_can_manage(sa, username)
    removed = db_remove_admin_user(username)
    if not removed:
        raise HTTPException(404, "Account not found or is the owner")
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


class MyEmailBody(BaseModel):
    new_email: str


@app.put("/api/me/email")
def change_my_email(body: MyEmailBody, current: dict = Depends(_get_current_user)):
    new_email = body.new_email.strip()
    if not new_email or "@" not in new_email:
        raise HTTPException(400, "A valid email is required")
    ok = db_update_admin_username(current["username"], new_email)
    if not ok:
        raise HTTPException(400, "That email is already in use")
    # Login name changed → the current token is now stale; client must re-login.
    return {"ok": True, "relogin": True}


# ── Bot Settings  (SuperAdmin only) ───────────────────────────────────────────

_HIDDEN_SETTINGS = {"BOT_CREATOR_USER_ID", "BOT_USERNAME", "BOT_CREATOR_GROUP_ID"}

@app.get("/api/settings")
def get_settings(sa: dict = Depends(_require_manager)):
    return [s for s in db_get_all_settings() if s["key"] not in _HIDDEN_SETTINGS]


class SettingBody(BaseModel):
    value: str


@app.put("/api/settings/{key}")
def update_setting(key: str, body: SettingBody, sa: dict = Depends(_require_manager)):
    if key in _HIDDEN_SETTINGS:
        raise HTTPException(403, "This setting cannot be changed from the panel")
    db_set_setting(key, body.value.strip())
    return {"ok": True}
