"""
auth_service.py — المصادقة: JWT + تشفير كلمات المرور
"""
import os
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Secret Key مشتق من ENCRYPTION_KEY ─────────────────────────────────────────
def _derive_secret_key() -> str:
    enc = os.getenv("ENCRYPTION_KEY", "")
    if enc:
        return hashlib.sha256(f"jwt-secret:{enc}".encode()).hexdigest()
    # fallback — يُنشأ مفتاح ثابت من اسم الجهاز
    machine = os.getenv("HOSTNAME", "affiliate-system")
    return hashlib.sha256(f"jwt-fallback:{machine}".encode()).hexdigest()

SECRET_KEY = _derive_secret_key()
ALGORITHM  = "HS256"
TOKEN_EXPIRE_DAYS = 7


# ── تشفير كلمات المرور (bcrypt) ─────────────────────────────────────────────────
def get_password_hash(password: str) -> str:
    from passlib.context import CryptContext
    ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    from passlib.context import CryptContext
    ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    try:
        return ctx.verify(plain, hashed)
    except Exception:
        return False


# ── توليد وتحقق JWT ────────────────────────────────────────────────────────────
def create_access_token(user_id: int, username: str, role: str) -> str:
    from jose import jwt
    expire = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {
        "sub":      str(user_id),
        "username": username,
        "role":     role,
        "exp":      expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        from jose import jwt, JWTError
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None


# ── مصادقة المستخدم من قاعدة البيانات ────────────────────────────────────────
def authenticate_user(username: str, password: str) -> Optional[dict]:
    from backend.config import SessionLocal
    from backend.models import User
    with SessionLocal() as db:
        user = db.query(User).filter(
            User.username == username,
            User.active == True,
        ).first()
        if not user:
            return None
        if not verify_password(password, user.password_hash):
            return None
        # تحديث آخر تسجيل دخول
        user.last_login = datetime.utcnow()
        db.commit()
        return {
            "id":       user.id,
            "username": user.username,
            "role":     user.role,
        }


# ── قراءة المستخدم من الـ Token ────────────────────────────────────────────────
def get_user_from_token(token: str) -> Optional[dict]:
    payload = decode_token(token)
    if not payload:
        return None
    from backend.config import SessionLocal
    from backend.models import User
    try:
        with SessionLocal() as db:
            user = db.get(User, int(payload["sub"]))
            if not user or not user.active:
                return None
            return {
                "id":       user.id,
                "username": user.username,
                "role":     user.role,
            }
    except Exception:
        return None
