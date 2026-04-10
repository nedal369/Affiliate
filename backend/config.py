"""
config.py — إدارة الإعدادات وقاعدة البيانات
"""
import os
import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── مسارات ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "database" / "affiliate.db"
ENV_PATH = BASE_DIR / ".env"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_PATH}"

# ── قاعدة البيانات ───────────────────────────────────────────────────────────
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── جدول الإعدادات ───────────────────────────────────────────────────────────
class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=True)


# ── جدول مفاتيح API ──────────────────────────────────────────────────────────
class ApiKey(Base):
    __tablename__ = "api_keys"
    service = Column(String(100), primary_key=True)
    encrypted_key = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=True)


# ── مفتاح التشفير ────────────────────────────────────────────────────────────
def _get_or_create_encryption_key() -> bytes:
    key_env = os.getenv("ENCRYPTION_KEY")
    if key_env:
        return key_env.encode()

    # أنشئ مفتاح جديد واحفظه في .env
    new_key = Fernet.generate_key()
    env_content = ""
    if ENV_PATH.exists():
        env_content = ENV_PATH.read_text()
    if "ENCRYPTION_KEY" not in env_content:
        with open(ENV_PATH, "a") as f:
            f.write(f"\nENCRYPTION_KEY={new_key.decode()}\n")
    logger.info("تم إنشاء مفتاح تشفير جديد وحفظه في .env")
    return new_key


_fernet: Optional[Fernet] = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_get_or_create_encryption_key())
    return _fernet


# ── دوال الإعدادات ────────────────────────────────────────────────────────────
def get_setting(key: str, default=None):
    with SessionLocal() as db:
        row = db.get(Setting, key)
        if row is None:
            return default
        try:
            return json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            return row.value


def set_setting(key: str, value):
    with SessionLocal() as db:
        row = db.get(Setting, key)
        serialized = json.dumps(value) if not isinstance(value, str) else value
        if row:
            row.value = serialized
        else:
            db.add(Setting(key=key, value=serialized))
        db.commit()


def get_all_settings() -> dict:
    with SessionLocal() as db:
        rows = db.query(Setting).all()
        result = {}
        for row in rows:
            try:
                result[row.key] = json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                result[row.key] = row.value
        return result


# ── دوال مفاتيح API ───────────────────────────────────────────────────────────
def get_api_key(service: str) -> Optional[str]:
    with SessionLocal() as db:
        row = db.get(ApiKey, service)
        if row is None or not row.encrypted_key:
            return None
        try:
            return get_fernet().decrypt(row.encrypted_key.encode()).decode()
        except Exception:
            logger.error(f"فشل فك تشفير مفتاح {service}")
            return None


def set_api_key(service: str, key: str):
    from datetime import datetime
    encrypted = get_fernet().encrypt(key.encode()).decode()
    with SessionLocal() as db:
        row = db.get(ApiKey, service)
        if row:
            row.encrypted_key = encrypted
            row.updated_at = datetime.utcnow()
        else:
            db.add(ApiKey(service=service, encrypted_key=encrypted, updated_at=datetime.utcnow()))
        db.commit()


def mask_api_key(key: Optional[str]) -> str:
    """أرجع نسخة مُخفَّاة: أول 4 أحرف + نقاط + آخر 4 أحرف"""
    if not key or len(key) < 8:
        return "••••••••"
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


def get_masked_key(service: str) -> str:
    return mask_api_key(get_api_key(service))


# ── قيم افتراضية ──────────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "system_enabled": True,
    "auto_approve": False,
    "fetch_interval_hours": 8,
    "min_discount_pct": 30,
    "min_rating": 4.0,
    "min_views": 100000,
    "enable_tiktok": True,
    "enable_instagram": True,
    "schedule_morning": True,
    "schedule_afternoon": True,
    "schedule_evening": True,
    "time_morning": "08:00",
    "time_afternoon": "14:00",
    "time_evening": "20:00",
    "enable_telegram": True,
    "enable_whatsapp": False,
    "enable_x": False,
    "attach_image": True,
    "manual_approval": True,
    "primary_llm": "claude-sonnet-4-5",
    "llm_fallback": True,
    "llm_priority": [
        "claude-sonnet-4-5",
        "gpt-4o-mini",
        "llama-3.3-70b-versatile",
    ],
    "amazon_marketplace": "SA",
}


def init_default_settings():
    """ابدأ بالإعدادات الافتراضية إذا لم تكن موجودة"""
    for key, value in DEFAULT_SETTINGS.items():
        existing = get_setting(key)
        if existing is None:
            set_setting(key, value)


# ── إنشاء الجداول ─────────────────────────────────────────────────────────────
def init_db():
    from backend import models  # noqa — ensure models are registered
    Base.metadata.create_all(bind=engine)
    init_default_settings()
    _create_default_admin()
    logger.info(f"قاعدة البيانات جاهزة: {DB_PATH}")


def _create_default_admin():
    """أنشئ حساب admin افتراضي عند أول تشغيل"""
    from backend.models import User
    from backend.services.auth_service import get_password_hash
    with SessionLocal() as db:
        exists = db.query(User).first()
        if exists:
            return  # يوجد مستخدمون — لا تفعل شيئاً
        admin = User(
            username="admin",
            password_hash=get_password_hash("admin123"),
            display_name="المدير",
            role="admin",
            active=True,
        )
        db.add(admin)
        db.commit()
        logger.warning(
            "✅ تم إنشاء حساب المدير الافتراضي — admin / admin123 — "
            "غيّر كلمة المرور فور تسجيل الدخول!"
        )
