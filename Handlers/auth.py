"""
Handlers/auth.py
Shared password hashing — uses bcrypt directly (no passlib).
Pre-hashes with SHA-256 to avoid bcrypt's 72-byte limit.
"""
import bcrypt
import hashlib


def _prep(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prep(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prep(plain), hashed.encode("utf-8"))
    except Exception:
        return False
