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
from passlib.context import CryptContext

# ── DB imports ──────────────────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from Handlers.db import (
    init_schema,
    db_get_admin_user,
    db_create_admin_user,
    db_get_all_admin_users,
    db_remove_admin_user,
    db_change_admin_password,
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
    db_generate_guids,
    db_get_all_groups,
    db_get_all_pending_invites,
)

# ── Config ───────────────────────────────────────────────────────────────────
SECRET_KEY    = os.environ.get("JWT_SECRET", "change-me-in-production-jwt-secret-key-32chars")
ALGORITHM     = "HS256"
TOKEN_EXPIRE  = 8  # hours

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "TheMonk@gmail.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "@onlySubs")

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2  = OAuth2PasswordBearer(tokenUrl="/api/login")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="OnlySubscriber Admin", docs_url=None, redoc_url=None)


@app.on_event("startup")
def startup():
    init_schema()
    # Create default admin user if not present
    existing = db_get_admin_user(ADMIN_USERNAME)
    if not existing:
        pw_hash = pwd_ctx.hash(ADMIN_PASSWORD)
        db_create_admin_user(ADMIN_USERNAME, pw_hash, role="superadmin")


# ── Auth helpers ─────────────────────────────────────────────────────────────

def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


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
    return {"username": username, "role": role}


def _require_superadmin(current: dict = Depends(_get_current_user)) -> dict:
    if current["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="SuperAdmin access required")
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


class CourseName(BaseModel):
    course_name: str


@app.post("/api/courses", status_code=201)
def add_course(body: CourseName, current_user: dict = Depends(_get_current_user)):
    name = body.course_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="course_name is required")
    db_save_course(name)
    return {"ok": True}


@app.delete("/api/courses/{course_id}")
def delete_course(course_id: int, current_user: dict = Depends(_get_current_user)):
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
def remove_user(user_id: int, current_user: dict = Depends(_get_current_user)):
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
    course: str
    start_date: str
    end_date: str


@app.post("/api/paid-users", status_code=201)
def add_paid_user(body: PaidUserBody, current_user: dict = Depends(_get_current_user)):
    db_add_paid_user(
        body.user_id,
        body.username or f"user_{body.user_id}",
        body.course,
        body.start_date,
        body.end_date,
    )
    return {"ok": True}


@app.delete("/api/paid-users/{user_id}")
def remove_paid_user(user_id: int, current_user: dict = Depends(_get_current_user)):
    db_remove_paid_user(user_id)
    return {"ok": True}


class EditPaidUserBody(BaseModel):
    course: str
    start_date: str
    end_date: str


@app.put("/api/paid-users/{user_id}")
def edit_paid_user(user_id: int, body: EditPaidUserBody, current_user: dict = Depends(_get_current_user)):
    db_edit_paid_user(user_id, body.course, body.start_date, body.end_date)
    return {"ok": True}


# ── GUIDs ─────────────────────────────────────────────────────────────────────

@app.get("/api/guids")
def guid_stats(current_user: dict = Depends(_get_current_user)):
    return db_get_guid_stats()


class GenerateGuidsBody(BaseModel):
    count: int = 100


@app.post("/api/guids/generate")
def generate_guids(body: GenerateGuidsBody, current_user: dict = Depends(_get_current_user)):
    if body.count < 1 or body.count > 10000:
        raise HTTPException(status_code=400, detail="count must be between 1 and 10000")
    inserted = db_generate_guids(body.count)
    return {"inserted": inserted}


# ── Groups ────────────────────────────────────────────────────────────────────

@app.get("/api/groups")
def list_groups(current_user: dict = Depends(_get_current_user)):
    groups = db_get_all_groups()
    for g in groups:
        for k, v in g.items():
            if hasattr(v, "isoformat"):
                g[k] = v.isoformat()
    return groups


# ── Pending Invites ───────────────────────────────────────────────────────────

@app.get("/api/pending-invites")
def list_pending_invites(current_user: dict = Depends(_get_current_user)):
    invites = db_get_all_pending_invites()
    for inv in invites:
        for k, v in inv.items():
            if hasattr(v, "isoformat"):
                inv[k] = v.isoformat()
    return invites


# ── Admin User Management  (SuperAdmin only) ──────────────────────────────────

@app.get("/api/admins")
def list_admins(sa: dict = Depends(_require_superadmin)):
    return db_get_all_admin_users()


class AddAdminBody(BaseModel):
    username: str
    password: str
    role: str = "admin"


@app.post("/api/admins", status_code=201)
def add_admin(body: AddAdminBody, sa: dict = Depends(_require_superadmin)):
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(400, "username and password required")
    if body.role not in ("admin", "superadmin"):
        raise HTTPException(400, "role must be 'admin' or 'superadmin'")
    pw_hash = pwd_ctx.hash(body.password)
    db_create_admin_user(username, pw_hash, role=body.role)
    return {"ok": True}


@app.delete("/api/admins/{username}")
def remove_admin(username: str, sa: dict = Depends(_require_superadmin)):
    if username == sa["username"]:
        raise HTTPException(400, "Cannot remove yourself")
    removed = db_remove_admin_user(username)
    if not removed:
        raise HTTPException(404, "Admin not found or is a superadmin")
    return {"ok": True}


class ChangePasswordBody(BaseModel):
    new_password: str


@app.put("/api/admins/{username}/password")
def change_password(username: str, body: ChangePasswordBody,
                    sa: dict = Depends(_require_superadmin)):
    user = db_get_admin_user(username)
    if not user:
        raise HTTPException(404, "Admin not found")
    if not body.new_password or len(body.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    new_hash = pwd_ctx.hash(body.new_password)
    db_change_admin_password(username, new_hash)
    return {"ok": True}
