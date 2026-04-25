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


# ── خريطة متغيرات البيئة لكل خدمة ───────────────────────────────────────────────
# المفاتيح المضبوطة كـ env vars تبقى ثابتة عبر جميع التحديثات على Render
ENV_VAR_MAP: dict[str, str] = {
    "anthropic":            "ANTHROPIC_API_KEY",
    "openai":               "OPENAI_API_KEY",
    "google":               "GOOGLE_API_KEY",
    "groq":                 "GROQ_API_KEY",
    "amazon_access_key":    "AMAZON_ACCESS_KEY",
    "amazon_secret_key":    "AMAZON_SECRET_KEY",
    "amazon_associate_tag": "AMAZON_ASSOCIATE_TAG",
    "apify":                "APIFY_API_KEY",
    "telegram_token":       "TELEGRAM_BOT_TOKEN",
    "telegram_channel_id":  "TELEGRAM_CHANNEL_ID",
    "ultramsg_instance":    "ULTRAMSG_INSTANCE",
    "ultramsg_token":       "ULTRAMSG_TOKEN",
    "whatsapp_number":      "WHATSAPP_NUMBER",
    "x_api_key":            "X_API_KEY",
    "x_api_secret":         "X_API_SECRET",
    "x_access_token":       "X_ACCESS_TOKEN",
    "x_access_secret":      "X_ACCESS_SECRET",
    # بدائل بدون Amazon PA API
    "rapidapi_key":         "RAPIDAPI_KEY",
    "openrouter":           "OPENROUTER_API_KEY",
}


# ── دوال مفاتيح API ───────────────────────────────────────────────────────────
def get_api_key(service: str) -> Optional[str]:
    # ① تحقق من env var أولاً — تبقى ثابتة عبر التحديثات على Render
    env_var = ENV_VAR_MAP.get(service)
    if env_var:
        env_val = os.getenv(env_var, "").strip()
        if env_val:
            return env_val

    # ② ارجع للقاعدة (للمفاتيح المدخلة يدوياً عبر الواجهة)
    with SessionLocal() as db:
        row = db.get(ApiKey, service)
        if row is None or not row.encrypted_key:
            return None
        try:
            return get_fernet().decrypt(row.encrypted_key.encode()).decode()
        except Exception:
            logger.error(f"فشل فك تشفير مفتاح {service}")
            return None


def is_key_from_env(service: str) -> bool:
    """هل المفتاح مصدره متغير البيئة؟"""
    env_var = ENV_VAR_MAP.get(service)
    return bool(env_var and os.getenv(env_var, "").strip())


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
    "primary_llm": "gemini-1.5-flash",
    "llm_fallback": True,
    "llm_priority": [
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "openrouter/meta-llama/llama-3.3-70b-instruct",
        "llama-3.3-70b-versatile",
        "gpt-4o-mini",
    ],
    "amazon_marketplace": "SA",
    "amazon_associate_tag": "nedal01-21",
    # ── النشر المتقدم ─────────────────────────────────────
    "posts_per_slot": 1,           # عدد المنشورات في كل موعد جدولة
    "post_interval_minutes": 30,   # الفترة بين المنشورات في نفس الموعد (دقيقة)
    # ── الاكتشاف التلقائي ─────────────────────────────────
    "auto_discover_enabled": False,
    "auto_discover_time": "07:00",
    "auto_discover_categories_count": 5,   # عدد التصنيفات المراد اكتشافها
    # ── السكرابر المجاني ──────────────────────────────────
    "amazon_data_source": "scraper",       # "scraper" | "rapidapi" | "auto"
    "scraper_cache_ttl_hours": 6,
}


def init_default_settings():
    """ابدأ بالإعدادات الافتراضية إذا لم تكن موجودة"""
    for key, value in DEFAULT_SETTINGS.items():
        existing = get_setting(key)
        if existing is None:
            set_setting(key, value)

    # إذا كان النموذج الأساسي لا يزال Claude وGoogle API موجود → حوّل لـ Gemini
    current_primary = get_setting("primary_llm", "")
    if current_primary and current_primary.startswith("claude") and os.getenv("GOOGLE_API_KEY"):
        set_setting("primary_llm", "gemini-1.5-flash")
        set_setting("llm_priority", DEFAULT_SETTINGS["llm_priority"])
        logger.info("تم تحديث النموذج الأساسي إلى gemini-1.5-flash")


# ── إنشاء الجداول ─────────────────────────────────────────────────────────────
def init_db():
    from backend import models  # noqa — ensure models are registered
    Base.metadata.create_all(bind=engine)
    _migrate_db()
    init_default_settings()
    # ملاحظة: إنشاء Admin يتم في الخلفية من main.py لتجنب blocking الـ startup
    logger.info(f"قاعدة البيانات جاهزة: {DB_PATH}")


def _migrate_db():
    """إضافة الأعمدة الجديدة إذا لم تكن موجودة — SQLite لا يدعم إضافتها تلقائياً"""
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE products ADD COLUMN is_deal_of_day BOOLEAN DEFAULT 0",
        "ALTER TABLE products ADD COLUMN total_clicks INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN telegram_post_id VARCHAR(100)",
        "ALTER TABLE products ADD COLUMN x_tweet_id VARCHAR(100)",
        "ALTER TABLE publish_logs ADD COLUMN post_id VARCHAR(100)",
        # جدول كاش السكرابر
        """CREATE TABLE IF NOT EXISTS scraper_cache (
            cache_key VARCHAR(255) PRIMARY KEY,
            payload TEXT NOT NULL,
            fetched_at DATETIME
        )""",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # العمود موجود مسبقاً — تجاهل


def create_default_admin():
    """أنشئ حساب admin افتراضي عند أول تشغيل — يُستدعى في الخلفية"""
    from backend.models import User
    with SessionLocal() as db:
        exists = db.query(User).first()
        if exists:
            return
        # استخدم sha256_crypt (built-in في passlib — لا يحتاج bcrypt)
        # وبالتالي لا يوجد C extension يمكن أن يفشل
        try:
            from passlib.context import CryptContext
            # جرّب bcrypt أولاً، ارجع لـ sha256_crypt إذا لم يتوفر
            try:
                ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
                pw_hash = ctx.hash("admin123")
            except Exception:
                ctx = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
                pw_hash = ctx.hash("admin123")
        except Exception as e:
            logger.error(f"خطأ في تشفير كلمة المرور: {e}")
            return

        admin = User(
            username="admin",
            password_hash=pw_hash,
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
