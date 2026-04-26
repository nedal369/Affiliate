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

# ── قاعدة البيانات ───────────────────────────────────────────────────────────
# الأولوية:
#   1) DATABASE_URL (PostgreSQL خارجي — يضمن استمرار البيانات على Render)
#   2) SQLite محلي على نفس الخادم (يضيع عند كل deploy على Render Free)
_DB_URL_ENV = os.getenv("DATABASE_URL", "").strip()

if _DB_URL_ENV:
    # Render و Heroku يُصدران postgres:// لكن SQLAlchemy 2.x يحتاج postgresql://
    if _DB_URL_ENV.startswith("postgres://"):
        _DB_URL_ENV = _DB_URL_ENV.replace("postgres://", "postgresql://", 1)
    DATABASE_URL = _DB_URL_ENV
    IS_SQLITE = DATABASE_URL.startswith("sqlite")
    if IS_SQLITE:
        engine = create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False},
            echo=False,
        )
    else:
        # PostgreSQL — pool_pre_ping يتحقق من الاتصال قبل كل استعلام لتجنب stale connections
        engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=False,
        )
    _db_label = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    logger.info(f"قاعدة البيانات الخارجية: {_db_label}")
else:
    DATABASE_URL = f"sqlite:///{DB_PATH}"
    IS_SQLITE = True
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    if os.getenv("ENVIRONMENT", "").lower() == "production":
        logger.warning(
            "⚠️ تشغيل SQLite في بيئة الإنتاج — البيانات ستضيع عند كل deploy على Render Free. "
            "اضبط متغير DATABASE_URL لقاعدة PostgreSQL خارجية للاحتفاظ بالبيانات."
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
    "primary_llm": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "llm_fallback": True,
    "llm_priority": [
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/deepseek/deepseek-r1:free",
        "openrouter/deepseek/deepseek-chat:free",
        "openrouter/qwen/qwen-2.5-72b-instruct:free",
        "openrouter/mistralai/mistral-7b-instruct:free",
        "openrouter/google/gemini-2.0-flash-001",
        "openrouter/meta-llama/llama-3.3-70b-instruct",
        "llama-3.3-70b-versatile",
        "gpt-4o-mini",
        "gemini-1.5-flash",
        "claude-haiku-4-5",
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


# النماذج المنتهية/المحذوفة التي يجب استبدالها تلقائياً
_DEPRECATED_MODELS = {
    "openrouter/google/gemini-2.0-flash-exp:free",  # محذوف من OpenRouter
}

# رقم إصدار قائمة الأولوية — نرفعه عند كل تحديث جوهري لإجبار التطبيق في قواعد البيانات الموجودة
_LLM_PRIORITY_VERSION = 3


def init_default_settings():
    """ابدأ بالإعدادات الافتراضية إذا لم تكن موجودة، وحدّث الإعدادات التي تغيّرت جوهرياً."""
    for key, value in DEFAULT_SETTINGS.items():
        existing = get_setting(key)
        if existing is None:
            set_setting(key, value)

    # إذا كانت نسخة القائمة قديمة أو تحتوي نماذج منتهية، استبدلها بالقائمة الجديدة
    stored_version = get_setting("llm_priority_version", 0)
    current_priority = get_setting("llm_priority", [])
    has_deprecated = (
        isinstance(current_priority, list)
        and any(m in _DEPRECATED_MODELS for m in current_priority)
    )
    if stored_version < _LLM_PRIORITY_VERSION or has_deprecated:
        set_setting("llm_priority", DEFAULT_SETTINGS["llm_priority"])
        set_setting("llm_priority_version", _LLM_PRIORITY_VERSION)
        logger.info(f"تم تحديث قائمة أولوية النماذج إلى الإصدار {_LLM_PRIORITY_VERSION}")

    # إذا كان primary_llm فارغاً أو نموذجاً منتهياً، اضبطه على الافتراضي الجديد
    current_primary = get_setting("primary_llm", "")
    if not current_primary or current_primary in _DEPRECATED_MODELS:
        set_setting("primary_llm", DEFAULT_SETTINGS["primary_llm"])
        logger.info(f"تم تعيين النموذج الأساسي: {DEFAULT_SETTINGS['primary_llm']}")


# ── إنشاء الجداول ─────────────────────────────────────────────────────────────
def init_db():
    from backend import models  # noqa — ensure models are registered
    Base.metadata.create_all(bind=engine)
    _migrate_db()
    init_default_settings()
    _seed_default_categories()
    # ملاحظة: إنشاء Admin يتم في الخلفية من main.py لتجنب blocking الـ startup
    logger.info(f"قاعدة البيانات جاهزة: {DB_PATH}")


def _seed_default_categories():
    """أضف التصنيفات الافتراضية إذا كانت القاعدة فارغة"""
    import json as _json
    from backend.models import DiscoveryCategory
    with SessionLocal() as db:
        if db.query(DiscoveryCategory).count() > 0:
            return
        defaults = [
            ("groceries", "المقاضي والبقالة", "🛒", ["coffee beans", "olive oil", "honey", "dates", "nuts mix", "spices set"], 1),
            ("kitchen_appliances", "الأجهزة المنزلية الصغيرة", "🍳", ["air fryer", "rice cooker", "blender", "coffee maker", "stand mixer", "electric kettle"], 2),
            ("kitchen_tools", "أدوات المطبخ", "🍴", ["non stick pan set", "kitchen knife set", "food storage containers", "cookware set", "cutting board", "measuring cups"], 3),
            ("cleaning", "التنظيف والعناية بالمنزل", "🧹", ["vacuum cleaner", "robot vacuum", "steam mop", "air purifier", "humidifier", "iron steamer"], 4),
            ("bedding", "أغطية وأقمشة المنزل", "🛏️", ["bed sheets king", "memory foam pillow", "comforter set", "blackout curtains", "bath towel set", "weighted blanket"], 5),
            ("home_decor", "ديكور المنزل", "🪴", ["wall clock", "led lights strip", "table lamp", "wall art", "artificial plants", "diffuser oil"], 6),
            ("perfumes", "العطور", "🌸", ["men perfume", "women perfume", "oud perfume", "body spray", "cologne", "incense bakhoor"], 7),
            ("skincare", "العناية بالبشرة", "✨", ["face cream", "vitamin c serum", "sunscreen", "face wash", "moisturizer", "anti aging cream"], 8),
            ("haircare", "العناية بالشعر", "💇", ["hair dryer", "hair straightener", "hair oil", "shampoo set", "hair clipper", "scalp brush"], 9),
            ("personal_care", "العناية الشخصية", "🪥", ["electric toothbrush", "shaver men", "epilator", "nail kit", "facial cleansing brush", "teeth whitening"], 10),
            ("audio", "الصوتيات والسماعات", "🎧", ["wireless earbuds", "noise cancelling headphones", "bluetooth speaker", "soundbar", "studio headphones", "portable speaker"], 11),
            ("phone_accessories", "اكسسوارات الجوال", "📱", ["phone charger fast", "power bank 20000", "phone case", "screen protector", "wireless charger", "car phone holder"], 12),
            ("smart_home", "المنزل الذكي", "💡", ["smart bulb", "smart plug", "security camera", "video doorbell", "smart speaker", "wifi extender"], 13),
            ("wearables", "الساعات الذكية واللياقة", "⌚", ["smart watch", "fitness tracker", "apple watch band", "heart rate monitor", "smart ring", "kids smart watch"], 14),
            ("gaming", "الألعاب والقيمنق", "🎮", ["gaming headset", "mechanical keyboard rgb", "gaming mouse", "gaming chair", "console controller", "gaming monitor"], 15),
            ("kids_toys", "ألعاب الأطفال", "🧸", ["lego building blocks", "educational toys", "remote control car", "kids puzzle", "doll house", "stuffed animals"], 16),
            ("baby", "مستلزمات الأطفال الرضع", "👶", ["baby monitor", "baby stroller", "diaper bag", "baby bottle warmer", "baby carrier", "kids tablet"], 17),
            ("sports", "الرياضة واللياقة", "🏋️", ["adjustable dumbbells", "yoga mat", "resistance bands set", "treadmill foldable", "exercise bike", "protein shaker"], 18),
            ("outdoor", "الرحلات والتخييم", "⛺", ["camping tent", "portable cooler", "sleeping bag", "camping chair", "outdoor grill", "hiking backpack"], 19),
            ("car", "اكسسوارات السيارة", "🚗", ["car vacuum", "dash cam", "car air freshener", "car seat cover", "tire inflator", "jump starter"], 20),
            ("office", "المكتب والعمل من المنزل", "💻", ["ergonomic office chair", "monitor stand", "desk lamp led", "laptop stand", "webcam hd", "wireless mouse"], 21),
            ("islamic", "المنتجات الإسلامية", "🕌", ["prayer mat", "quran reader pen", "digital tasbih", "azan clock", "prayer rug travel", "islamic decor"], 22),
        ]
        for cat_id, name_ar, icon, keywords, order in defaults:
            db.add(DiscoveryCategory(
                cat_id=cat_id, name_ar=name_ar, icon=icon,
                keywords=_json.dumps(keywords, ensure_ascii=False),
                active=True, sort_order=order,
            ))
        db.commit()
        logger.info(f"تم إضافة {len(defaults)} تصنيف افتراضي")


def _migrate_db():
    """إضافة الأعمدة الجديدة إذا لم تكن موجودة.
    على Postgres يكفي Base.metadata.create_all لإنشاء الجداول الجديدة،
    لكن إضافة أعمدة لجداول موجودة تحتاج ALTER TABLE صريح.
    """
    from sqlalchemy import text

    if IS_SQLITE:
        # SQLite: لا يدعم IF NOT EXISTS على ALTER TABLE — نتجاهل الأخطاء
        migrations = [
            "ALTER TABLE products ADD COLUMN is_deal_of_day BOOLEAN DEFAULT 0",
            "ALTER TABLE products ADD COLUMN total_clicks INTEGER DEFAULT 0",
            "ALTER TABLE products ADD COLUMN telegram_post_id VARCHAR(100)",
            "ALTER TABLE products ADD COLUMN x_tweet_id VARCHAR(100)",
            "ALTER TABLE publish_logs ADD COLUMN post_id VARCHAR(100)",
        ]
    else:
        # PostgreSQL: يدعم IF NOT EXISTS على ALTER TABLE منذ 9.6
        migrations = [
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_deal_of_day BOOLEAN DEFAULT FALSE",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS total_clicks INTEGER DEFAULT 0",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS telegram_post_id VARCHAR(100)",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS x_tweet_id VARCHAR(100)",
            "ALTER TABLE publish_logs ADD COLUMN IF NOT EXISTS post_id VARCHAR(100)",
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
