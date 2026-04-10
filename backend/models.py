"""
models.py — نماذج قاعدة البيانات (SQLAlchemy)
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text,
    DateTime, ForeignKey, Index,
)
from backend.config import Base


class Product(Base):
    """المنتجات — قائمة الانتظار والمنشورة"""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    title_ar = Column(String(500), nullable=True)
    source = Column(String(50), nullable=True)          # tiktok | instagram | keyword
    source_account = Column(String(200), nullable=True)
    source_views = Column(Integer, default=0)
    amazon_asin = Column(String(20), nullable=True)
    amazon_url = Column(Text, nullable=True)
    affiliate_url = Column(Text, nullable=True)
    price_original = Column(Float, default=0.0)
    price_current = Column(Float, default=0.0)
    discount_pct = Column(Integer, default=0)
    rating = Column(Float, default=0.0)
    rating_count = Column(Integer, default=0)
    image_url = Column(Text, nullable=True)
    ai_content = Column(Text, nullable=True)
    ai_model_used = Column(String(100), nullable=True)
    status = Column(String(50), default="pending")      # pending|approved|rejected|published|failed
    created_at = Column(DateTime, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_products_status", "status"),
        Index("ix_products_asin", "amazon_asin"),
    )


class PublishLog(Base):
    """سجل النشر على القنوات"""
    __tablename__ = "publish_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    channel = Column(String(50), nullable=False)        # telegram|whatsapp|x
    success = Column(Boolean, default=False)
    clicks = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    published_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_publish_logs_published_at", "published_at"),
    )


class WatchedAccount(Base):
    """الحسابات المراقبة على TikTok/Instagram"""
    __tablename__ = "watched_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(50), nullable=False)       # tiktok | instagram
    username = Column(String(200), nullable=False)
    active = Column(Boolean, default=True)
    last_checked = Column(DateTime, nullable=True)
    posts_found = Column(Integer, default=0)


class TrendKeyword(Base):
    """الكلمات المفتاحية للبحث"""
    __tablename__ = "trend_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(200), nullable=False)
    platform = Column(String(50), nullable=True)
    active = Column(Boolean, default=True)
    last_used = Column(DateTime, nullable=True)
