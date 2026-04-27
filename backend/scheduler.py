"""
scheduler.py — جدولة المهام التلقائية (APScheduler)
"""
import asyncio
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

_scheduler = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler(timezone="Asia/Riyadh")
    return _scheduler


# ── المهمة الرئيسية: جلب الترندات + البحث في أمازون ─────────────────────────────
async def fetch_and_queue():
    """
    1. جلب الترندات
    2. البحث في أمازون عن كل منتج
    3. توليد المحتوى بالذكاء الاصطناعي
    4. إضافة للقائمة (pending) — النشر يتم لاحقاً حسب الجدولة
    """
    from backend.config import get_setting, SessionLocal
    from backend.models import Product
    from backend.services.trend_service import fetch_trends, extract_product_name_with_ai
    from backend.services.amazon_service import search_product, is_product_posted_before
    from backend.services.ai_service import generate_content
    from backend.services.publisher import publish_product

    if not get_setting("system_enabled", True):
        logger.info("النظام متوقف — تخطي دورة الجلب")
        return

    logger.info("بدء دورة جلب المنتجات...")
    min_discount = get_setting("min_discount_pct", 30)
    min_rating = get_setting("min_rating", 4.0)
    min_views = get_setting("min_views", 100000)
    auto_approve = get_setting("auto_approve", False)
    primary_llm = get_setting("primary_llm", "")

    trends = await fetch_trends()
    logger.info(f"تم جلب {len(trends)} ترند")

    semaphore = asyncio.Semaphore(3)

    async def process_trend(trend: dict):
        async with semaphore:
            await _process_single_trend(
                trend, min_discount, min_rating, min_views,
                auto_approve, primary_llm
            )

    await asyncio.gather(*[process_trend(t) for t in trends], return_exceptions=True)
    logger.info("انتهت دورة الجلب")


async def _process_single_trend(
    trend: dict,
    min_discount: int,
    min_rating: float,
    min_views: int,
    auto_approve: bool,
    primary_llm: str,
):
    from backend.config import SessionLocal, get_setting
    from backend.models import Product
    from backend.services.trend_service import extract_product_name_with_ai
    from backend.services.amazon_service import search_product, has_published_recently
    from backend.services.ai_service import generate_content
    from backend.services.publisher import publish_product

    try:
        if trend.get("source_views", 0) < min_views and trend.get("source") != "keyword":
            return

        title = trend.get("title", "")
        if trend.get("caption") and not trend.get("title_ar"):
            title = await extract_product_name_with_ai(trend.get("caption", title))

        amazon = await search_product(title)
        if not amazon:
            return

        if amazon.get("discount_pct", 0) < min_discount:
            return
        if amazon.get("rating", 0) < min_rating:
            return

        dedup_hours = get_setting("autopilot_dedup_hours", 72)
        if has_published_recently(amazon["asin"], hours=dedup_hours):
            logger.info(f"المنتج {amazon['asin']} نُشر خلال آخر {dedup_hours} ساعة — تخطي")
            return

        content, model_used = await generate_content(amazon, llm=primary_llm or "auto")

        with SessionLocal() as db:
            product = Product(
                title=amazon.get("title", title),
                title_ar=trend.get("title_ar", ""),
                source=trend.get("source", "keyword"),
                source_account=trend.get("source_account", ""),
                source_views=trend.get("source_views", 0),
                amazon_asin=amazon["asin"],
                amazon_url=amazon.get("amazon_url", ""),
                affiliate_url=amazon["affiliate_url"],
                price_original=amazon.get("price_original", 0),
                price_current=amazon.get("price_current", 0),
                discount_pct=amazon.get("discount_pct", 0),
                rating=amazon.get("rating", 0),
                rating_count=amazon.get("rating_count", 0),
                image_url=amazon.get("image_url", ""),
                ai_content=content,
                ai_model_used=model_used,
                status="approved" if auto_approve else "pending",
            )
            db.add(product)
            db.commit()
            db.refresh(product)
            product_id = product.id

        logger.info(f"تم إضافة المنتج {amazon['asin']} — الحالة: {product.status}")

        # نشر فوري فقط إذا كانت الموافقة التلقائية مفعّلة
        if auto_approve:
            await publish_product(product_id)

    except Exception as exc:
        logger.error(f"خطأ في معالجة الترند {trend.get('title', '')}: {exc}")


# ── نشر المنتجات المعتمدة في الموعد المجدول ──────────────────────────────────────
async def publish_approved_products():
    """
    تُشغَّل في كل موعد مجدول.
    تنشر المنتجات المعتمدة (approved) بالترتيب:
    — عرض اليوم أولاً، ثم الأقدم.
    """
    from backend.config import get_setting, SessionLocal
    from backend.models import Product
    from backend.services.publisher import publish_product

    if not get_setting("system_enabled", True):
        return

    posts_per_slot = get_setting("posts_per_slot", 1)
    interval_min = get_setting("post_interval_minutes", 30)

    with SessionLocal() as db:
        products = (
            db.query(Product)
            .filter(Product.status == "approved")
            .order_by(Product.is_deal_of_day.desc(), Product.created_at.asc())
            .limit(posts_per_slot)
            .all()
        )
        ids = [p.id for p in products]

    if not ids:
        logger.info("لا توجد منتجات معتمدة للنشر في هذا الموعد")
        return

    logger.info(f"نشر {len(ids)} منتج معتمد...")
    for i, pid in enumerate(ids):
        if i > 0 and interval_min > 0:
            await asyncio.sleep(interval_min * 60)
        try:
            await publish_product(pid)
        except Exception as exc:
            logger.error(f"خطأ في نشر المنتج {pid}: {exc}")


# ── الاكتشاف التلقائي اليومي للعروض ─────────────────────────────────────────────
async def auto_discover_daily():
    """اكتشاف تلقائي يومي للعروض وإضافتها لقائمة الانتظار"""
    from backend.config import get_setting

    if not get_setting("auto_discover_enabled", False):
        return

    categories_count = get_setting("auto_discover_categories_count", 5)
    logger.info(f"بدء الاكتشاف التلقائي اليومي — {categories_count} تصنيف")

    try:
        from backend.services.deals_finder_service import auto_discover_and_queue
        result = await auto_discover_and_queue(max_categories=categories_count)
        logger.info(f"اكتشاف تلقائي: {result.get('added', 0)} منتج أُضيف للانتظار")
    except Exception as exc:
        logger.error(f"خطأ في الاكتشاف التلقائي: {exc}")


# ── Autopilot: 2 تصنيف × 5 منتجات × 3 فترات = 30 منتج/يوم ─────────────────────
async def run_autopilot_slot():
    """Slot واحد من الـ autopilot:
      1) اختر 2 تصنيف لم يُستخدما اليوم
      2) لكل تصنيف: اجلب أعلى 5 منتجات (مع استبعاد المنشور خلال 72h)
      3) أنشئ المنتجات بحالة approved في DB
      4) انشرها بفاصل post_interval_minutes بين كل منشور
      5) قبل كل نشر: تحقق نهائي من 72h لمنع الـ race
    """
    from backend.config import SessionLocal, get_setting
    from backend.models import Product
    from backend.services.deals_finder_service import (
        discover_deals, pick_unused_categories_for_today, mark_categories_used,
    )
    from backend.services.amazon_service import (
        has_published_recently, recent_published_asins,
    )
    from backend.services.publisher import publish_product
    from backend.services.ai_service import generate_content, generate_deal_of_day_content

    if not get_setting("system_enabled", True):
        logger.info("Autopilot: النظام معطّل — تخطي")
        return
    if not get_setting("autopilot_enabled", False):
        logger.info("Autopilot: غير مفعّل — تخطي")
        return

    cats_per_slot = int(get_setting("autopilot_cats_per_slot", 2))
    products_per_cat = int(get_setting("autopilot_products_per_cat", 5))
    interval_min = int(get_setting("post_interval_minutes", 30))
    dedup_hours = int(get_setting("autopilot_dedup_hours", 72))

    # ① اختر تصنيفات لم تُستخدم اليوم وعلّمها فوراً (لمنع slot ثانٍ من اختيارها)
    cats = pick_unused_categories_for_today(cats_per_slot)
    if not cats:
        logger.info("Autopilot: لا توجد تصنيفات متاحة لليوم — تم استخدامها كلها")
        return
    cat_db_ids = [c["id"] for c in cats]
    cat_ids = [c["cat_id"] for c in cats]
    cat_names = [c["name_ar"] for c in cats]
    mark_categories_used(cat_db_ids)
    logger.info(f"Autopilot: اختار {cat_names}")

    # ② اجلب الـ ASINs المنشورة مؤخراً مرة واحدة
    recent = recent_published_asins(hours=dedup_hours)
    seen_map = {cid: list(recent) for cid in cat_ids}

    # ③ اكتشف العروض
    try:
        deals_by_cat = await discover_deals(cat_ids, seen_asins=seen_map)
    except Exception as exc:
        logger.error(f"Autopilot: فشل discover_deals: {exc}")
        return

    # ④ أنشئ المنتجات بحالة approved
    primary_llm = get_setting("primary_llm", "auto") or "auto"
    queue: list[tuple[int, str]] = []
    for cid, deals in deals_by_cat.items():
        for deal in deals[:products_per_cat]:
            asin = deal.get("asin", "")
            if not asin or has_published_recently(asin, hours=dedup_hours):
                continue
            try:
                content, model_used = await generate_content(deal, llm=primary_llm)
            except Exception as exc:
                logger.warning(f"Autopilot: فشل توليد محتوى {asin}: {exc}")
                continue
            try:
                with SessionLocal() as db:
                    product = Product(
                        title=deal.get("title", ""),
                        title_ar=deal.get("title", ""),
                        source="autopilot",
                        amazon_asin=asin,
                        amazon_url=deal.get("amazon_url", ""),
                        affiliate_url=deal.get("affiliate_url", ""),
                        price_original=deal.get("price_original", 0),
                        price_current=deal.get("price_current", 0),
                        discount_pct=deal.get("discount_pct", 0),
                        rating=deal.get("rating", 0),
                        rating_count=deal.get("rating_count", 0),
                        image_url=deal.get("image_url", ""),
                        ai_content=content,
                        ai_model_used=model_used,
                        status="approved",
                    )
                    db.add(product)
                    db.commit()
                    db.refresh(product)
                    queue.append((product.id, asin))
            except Exception as exc:
                logger.error(f"Autopilot: فشل حفظ المنتج {asin}: {exc}")

    if not queue:
        logger.warning(f"Autopilot: لم تُنتج أي منتجات قابلة للنشر في {cat_names}")
        return

    logger.info(f"Autopilot: سينشر {len(queue)} منتج بفاصل {interval_min} دقيقة")

    # ⑤ انشر بفاصل زمني، مع تحقق نهائي قبل كل منشور
    for i, (pid, asin) in enumerate(queue):
        if i > 0 and interval_min > 0:
            await asyncio.sleep(interval_min * 60)
        if has_published_recently(asin, hours=dedup_hours):
            logger.info(f"Autopilot: تجاوز ASIN {asin} (نُشر خلال {dedup_hours}h من قبل slot آخر)")
            continue
        try:
            await publish_product(pid)
        except Exception as exc:
            logger.error(f"Autopilot: فشل نشر المنتج {pid}: {exc}")

    logger.info(f"Autopilot: انتهى slot ({cat_names})")


# ── تنظيف السجلات القديمة ─────────────────────────────────────────────────────────
async def cleanup_old_records():
    from backend.config import SessionLocal, get_setting
    from backend.models import Product, PublishLog, ScraperCache, AuditLog
    from datetime import timedelta

    cutoff_90d = datetime.utcnow() - timedelta(days=90)
    cutoff_180d = datetime.utcnow() - timedelta(days=180)
    cache_ttl_hours = int(get_setting("scraper_cache_ttl_hours", 6))
    cutoff_cache = datetime.utcnow() - timedelta(hours=cache_ttl_hours)

    try:
        with SessionLocal() as db:
            # سجلات النشر القديمة (>90 يوم)
            old_logs = db.query(PublishLog).filter(PublishLog.published_at < cutoff_90d).count()
            db.query(PublishLog).filter(PublishLog.published_at < cutoff_90d).delete()

            # المنتجات المرفوضة/الفاشلة القديمة
            old_products = (
                db.query(Product)
                .filter(Product.created_at < cutoff_90d, Product.status.in_(["rejected", "failed"]))
                .count()
            )
            db.query(Product).filter(
                Product.created_at < cutoff_90d, Product.status.in_(["rejected", "failed"])
            ).delete()

            # كاش السكرابر المنتهي
            old_cache = db.query(ScraperCache).filter(ScraperCache.fetched_at < cutoff_cache).count()
            db.query(ScraperCache).filter(ScraperCache.fetched_at < cutoff_cache).delete()

            # سجلات التدقيق الأقدم من 180 يوم
            old_audit = db.query(AuditLog).filter(AuditLog.created_at < cutoff_180d).count()
            db.query(AuditLog).filter(AuditLog.created_at < cutoff_180d).delete()

            db.commit()
        logger.info(
            f"تنظيف: {old_logs} نشر، {old_products} منتج قديم، "
            f"{old_cache} كاش منتهي، {old_audit} audit log"
        )
    except Exception as exc:
        logger.error(f"خطأ في تنظيف السجلات: {exc}")


# ── إعداد الجدولة ──────────────────────────────────────────────────────────────
def setup_scheduler():
    from backend.config import get_setting

    scheduler = get_scheduler()

    # تنظيف يومي الساعة 3 صباحاً
    scheduler.add_job(
        cleanup_old_records,
        trigger="cron",
        hour=3,
        minute=0,
        id="cleanup",
        replace_existing=True,
    )

    refresh_schedule()

    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler بدأ")


_AUTOPILOT_JOB_IDS = ["autopilot_morning", "autopilot_afternoon", "autopilot_evening"]
_LEGACY_JOB_IDS = [
    "fetch_morning", "fetch_afternoon", "fetch_evening",
    "publish_morning", "publish_afternoon", "publish_evening",
    "auto_discover",
]


def refresh_schedule():
    """تحديث مواعيد الجدولة من الإعدادات الحالية.
    وضعان:
      • autopilot_enabled=True  → 3 jobs (autopilot_*) فقط، لا fetch/publish منفصل.
      • autopilot_enabled=False → السلوك القديم (fetch + publish منفصلان).
    """
    from backend.config import get_setting

    scheduler = get_scheduler()

    # احذف كل المهام القديمة (من كلا الوضعين)
    for job_id in _LEGACY_JOB_IDS + _AUTOPILOT_JOB_IDS:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

    def parse_time(t: str):
        parts = t.split(":")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

    autopilot_on = bool(get_setting("autopilot_enabled", False))

    if autopilot_on:
        # ── وضع Autopilot: 3 jobs، كل واحد ينفّذ slot كامل ──
        slot_specs = [
            ("autopilot_morning",   "schedule_morning",   "time_morning",   "08:00"),
            ("autopilot_afternoon", "schedule_afternoon", "time_afternoon", "14:00"),
            ("autopilot_evening",   "schedule_evening",   "time_evening",   "20:00"),
        ]
        for job_id, sched_key, time_key, default_time in slot_specs:
            if get_setting(sched_key, True):
                h, m = parse_time(get_setting(time_key, default_time))
                scheduler.add_job(
                    run_autopilot_slot, "cron", hour=h, minute=m,
                    id=job_id, replace_existing=True,
                    misfire_grace_time=600, coalesce=True,
                )
        logger.info("تم تفعيل Autopilot — 3 jobs مجدولة")
    else:
        # ── الوضع القديم: fetch + publish منفصلان لكل slot ──
        if get_setting("schedule_morning", True):
            h, m = parse_time(get_setting("time_morning", "08:00"))
            scheduler.add_job(fetch_and_queue, "cron", hour=h, minute=m,
                              id="fetch_morning", replace_existing=True)
            scheduler.add_job(publish_approved_products, "cron", hour=h, minute=m,
                              id="publish_morning", replace_existing=True)

        if get_setting("schedule_afternoon", True):
            h, m = parse_time(get_setting("time_afternoon", "14:00"))
            scheduler.add_job(fetch_and_queue, "cron", hour=h, minute=m,
                              id="fetch_afternoon", replace_existing=True)
            scheduler.add_job(publish_approved_products, "cron", hour=h, minute=m,
                              id="publish_afternoon", replace_existing=True)

        if get_setting("schedule_evening", True):
            h, m = parse_time(get_setting("time_evening", "20:00"))
            scheduler.add_job(fetch_and_queue, "cron", hour=h, minute=m,
                              id="fetch_evening", replace_existing=True)
            scheduler.add_job(publish_approved_products, "cron", hour=h, minute=m,
                              id="publish_evening", replace_existing=True)

        if get_setting("auto_discover_enabled", False):
            h, m = parse_time(get_setting("auto_discover_time", "07:00"))
            scheduler.add_job(auto_discover_daily, "cron", hour=h, minute=m,
                              id="auto_discover", replace_existing=True)
        logger.info("تم تحديث الجدولة (الوضع اليدوي)")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler أُوقف")
