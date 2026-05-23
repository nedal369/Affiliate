"""
tracker.py — تتبع النقرات والعمولات (Placeholder للمستقبل)
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


async def update_click_counts():
    """تحديث عدد النقرات — placeholder للاتصال بـ Amazon API مستقبلاً"""
    logger.info("تحديث عدد النقرات (placeholder)")


async def get_today_stats() -> dict:
    """إحصاءات اليوم"""
    import asyncio
    from backend.config import SessionLocal
    from backend.models import Product, PublishLog

    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())

    def _query():
        with SessionLocal() as db:
            published_today = (
                db.query(Product)
                .filter(Product.published_at >= today_start, Product.status == "published")
                .count()
            )
            pending = db.query(Product).filter(Product.status == "pending").count()
            total_clicks = (
                db.query(PublishLog)
                .filter(PublishLog.published_at >= today_start)
                .with_entities(__import__("sqlalchemy").func.sum(PublishLog.clicks))
                .scalar() or 0
            )
            return published_today, pending, int(total_clicks)

    published_today, pending, total_clicks = await asyncio.to_thread(_query)

    return {
        "published_today": published_today,
        "pending_count": pending,
        "clicks_today": total_clicks,
        "estimated_commission": round(float(total_clicks) * 0.05 * 8.5, 2),
    }


async def get_analytics_data(days: int = 30) -> dict:
    """بيانات التحليلات للرسم البياني"""
    import asyncio
    from backend.config import SessionLocal
    from backend.models import Product, PublishLog
    import sqlalchemy as sa

    since = datetime.utcnow() - timedelta(days=days)

    def _query():
        with SessionLocal() as db:
            daily_rows = (
                db.query(
                    sa.func.date(Product.published_at).label("date"),
                    sa.func.count(Product.id).label("count"),
                )
                .filter(Product.published_at >= since, Product.status == "published")
                .group_by(sa.func.date(Product.published_at))
                .order_by("date")
                .all()
            )
            source_rows = (
                db.query(Product.source, sa.func.count(Product.id).label("count"))
                .filter(Product.created_at >= since)
                .group_by(Product.source)
                .all()
            )
            channel_rows = (
                db.query(
                    PublishLog.channel,
                    sa.func.sum(PublishLog.clicks).label("clicks"),
                    sa.func.count(PublishLog.id).label("posts"),
                )
                .filter(PublishLog.published_at >= since)
                .group_by(PublishLog.channel)
                .all()
            )
            top_products = (
                db.query(
                    Product.title,
                    sa.func.sum(PublishLog.clicks).label("total_clicks"),
                )
                .join(PublishLog, PublishLog.product_id == Product.id)
                .filter(PublishLog.published_at >= since)
                .group_by(Product.id)
                .order_by(sa.desc("total_clicks"))
                .limit(10)
                .all()
            )
            return (
                [{"date": str(r.date), "count": r.count} for r in daily_rows],
                [{"source": r.source or "unknown", "count": r.count} for r in source_rows],
                [{"channel": r.channel, "clicks": int(r.clicks or 0), "posts": r.posts} for r in channel_rows],
                [{"title": r.title[:60], "clicks": int(r.total_clicks or 0)} for r in top_products],
            )

    daily, sources, channels, top_products = await asyncio.to_thread(_query)
    return {"daily": daily, "sources": sources, "channels": channels, "top_products": top_products}
