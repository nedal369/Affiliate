"""
admin_panel/auth.py — مصادقة مالك/مشغّل منصة SaaS.

منفصل تماماً عن backend/services/auth_service.py الذي يخص instances العملاء.
يستخدم JWT في httpOnly cookie + bcrypt لكلمات المرور.
"""
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from admin_panel.models import MasterSession, Operator

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7


def _derive_secret() -> str:
    """يشتق مفتاح JWT من ADMIN_JWT_SECRET أو ADMIN_API_KEY أو ENCRYPTION_KEY."""
    for env_name in ("ADMIN_JWT_SECRET", "ADMIN_API_KEY", "ENCRYPTION_KEY"):
        val = os.getenv(env_name, "").strip()
        if val:
            return hashlib.sha256(f"saas-owner-jwt:{val}".encode()).hexdigest()
    # fallback مستقر عبر التشغيلات لكنه ضعيف — يجب ضبط ADMIN_JWT_SECRET في الإنتاج
    machine = os.getenv("HOSTNAME", "admin-panel")
    return hashlib.sha256(f"admin-jwt-fallback:{machine}".encode()).hexdigest()


SECRET_KEY = _derive_secret()


def hash_password(password: str) -> str:
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


def create_token(operator_id: int, username: str, role: str) -> str:
    from jose import jwt
    payload = {
        "sub":      str(operator_id),
        "username": username,
        "role":     role,
        "scope":    "saas-operator",
        "exp":      datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS),
        "iat":      datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        from jose import jwt
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None


def authenticate_operator(username: str, password: str) -> Optional[dict]:
    username = (username or "").strip().lower()
    with MasterSession() as db:
        op = db.query(Operator).filter(Operator.username == username).first()
        if not op or not op.active:
            return None
        if not verify_password(password, op.password_hash):
            return None
        op.last_login_at = datetime.utcnow()
        db.commit()
        return {"id": op.id, "username": op.username, "role": op.role}


def get_operator_from_token(token: str) -> Optional[dict]:
    payload = decode_token(token)
    if not payload or payload.get("scope") != "saas-operator":
        return None
    try:
        op_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        return None
    if not op_id:
        return None
    with MasterSession() as db:
        op = db.get(Operator, op_id)
        if not op or not op.active:
            return None
        return {"id": op.id, "username": op.username, "role": op.role}


def create_default_owner() -> None:
    """يُنشئ المالك الافتراضي للمنصة عند أول تشغيل (إذا لم يوجد أي operator).
    يُسحب من متغيرات البيئة:
      SAAS_OWNER_USERNAME — افتراضي: "owner"
      SAAS_OWNER_PASSWORD — افتراضي: مولّد عشوائياً ويُطبع في الـ logs
      SAAS_OWNER_EMAIL    — اختياري
    """
    with MasterSession() as db:
        if db.query(Operator).first():
            return  # موجود مسبقاً — لا تتدخل

        username = os.getenv("SAAS_OWNER_USERNAME", "owner").strip().lower() or "owner"
        password = os.getenv("SAAS_OWNER_PASSWORD", "").strip()
        email = os.getenv("SAAS_OWNER_EMAIL", "").strip() or None

        generated = False
        if not password:
            password = secrets.token_urlsafe(12)
            generated = True

        op = Operator(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role="super_admin",
            active=True,
        )
        db.add(op)
        db.commit()

    if generated:
        logger.warning(
            "═" * 70 + "\n"
            f"✅ تم إنشاء حساب مالك المنصة الافتراضي:\n"
            f"   اسم المستخدم: {username}\n"
            f"   كلمة المرور:  {password}\n"
            f"   ⚠️  هذه كلمة مرور مولّدة عشوائياً — احفظها فوراً وغيّرها بعد أول دخول.\n"
            f"   ⚠️  لتعيين كلمة مرور دائمة: اضبط SAAS_OWNER_PASSWORD قبل أول تشغيل.\n"
            + "═" * 70
        )
    else:
        logger.info(f"✅ تم إنشاء حساب مالك المنصة: {username} (كلمة المرور من SAAS_OWNER_PASSWORD)")
