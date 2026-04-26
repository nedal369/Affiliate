"""
admin_panel/models.py — قاعدة بيانات المدير المركزية لإدارة العملاء (SaaS).

تُستخدم في instance منفصلة عن instances العملاء — لا تُربط بقواعد بياناتهم.
الهدف: تتبع العملاء، خططهم، استهلاكهم، وحالة instances الخاصة بهم على Render.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text,
    DateTime, ForeignKey, Index,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship
from sqlalchemy import create_engine
import os


class MasterBase(DeclarativeBase):
    pass


class Operator(MasterBase):
    """مالك/مشغّل منصة الـ SaaS — مختلف تماماً عن admin instances العملاء.
    هذا الحساب يدخل لوحة الأدمن المركزية لإدارة جميع العملاء.
    """
    __tablename__ = "operators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="super_admin")  # super_admin | operator
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_operators_username", "username"),
    )


class Client(MasterBase):
    """العميل = حساب SaaS واحد، له instance خاصة على Render"""
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    plan = Column(String(50), default="free")              # free | pro | enterprise
    status = Column(String(20), default="pending")          # pending | active | suspended | cancelled

    # Render integration
    render_service_id = Column(String(100), nullable=True)
    render_db_id = Column(String(100), nullable=True)
    service_url = Column(String(255), nullable=True)        # https://client-xyz.onrender.com

    # Billing
    stripe_customer_id = Column(String(255), nullable=True)
    monthly_fee = Column(Float, default=0.0)

    created_at = Column(DateTime, default=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)
    suspended_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    usage_logs = relationship("UsageLog", back_populates="client", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_clients_email", "email"),
        Index("ix_clients_status", "status"),
    )


class UsageLog(MasterBase):
    """تتبع استهلاك العميل الشهري — لتطبيق حدود الخطط"""
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    period = Column(String(7), nullable=False)              # "2026-04"
    products_published = Column(Integer, default=0)
    ai_calls = Column(Integer, default=0)
    api_calls = Column(Integer, default=0)

    client = relationship("Client", back_populates="usage_logs")

    __table_args__ = (
        Index("ix_usage_client_period", "client_id", "period", unique=True),
    )


# ── إعداد المحرّك ────────────────────────────────────────────────────────────
MASTER_DB_URL = os.getenv(
    "MASTER_DATABASE_URL",
    "sqlite:///./admin_panel.db",
)
if MASTER_DB_URL.startswith("postgres://"):
    MASTER_DB_URL = MASTER_DB_URL.replace("postgres://", "postgresql://", 1)

is_sqlite = MASTER_DB_URL.startswith("sqlite")
master_engine = create_engine(
    MASTER_DB_URL,
    connect_args={"check_same_thread": False} if is_sqlite else {},
    pool_pre_ping=not is_sqlite,
)
MasterSession = sessionmaker(bind=master_engine, autoflush=False, autocommit=False)


# ── خطط الاشتراك ─────────────────────────────────────────────────────────────
SUBSCRIPTION_PLANS = {
    "free": {
        "name": "مجاني",
        "price_usd": 0,
        "products_per_month": 30,
        "ai_calls_per_month": 100,
        "channels": 1,
        "support": "community",
    },
    "pro": {
        "name": "احترافي",
        "price_usd": 49,
        "products_per_month": 500,
        "ai_calls_per_month": 2000,
        "channels": 3,
        "support": "email",
    },
    "enterprise": {
        "name": "مؤسسات",
        "price_usd": 149,
        "products_per_month": -1,    # -1 = لا حد
        "ai_calls_per_month": -1,
        "channels": 10,
        "support": "priority",
    },
}


def init_master_db():
    """ينشئ جداول قاعدة المدير عند أول تشغيل."""
    MasterBase.metadata.create_all(bind=master_engine)
