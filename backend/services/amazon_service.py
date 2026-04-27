"""
amazon_service.py — البحث في أمازون وتوليد رابط الإحالة
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
from functools import lru_cache

logger = logging.getLogger(__name__)

# ── Cache للنتائج (ASIN → نتيجة + وقت) ────────────────────────────────────────
_asin_cache: dict[str, tuple[dict, datetime]] = {}
CACHE_TTL_HOURS = 6


def _is_cache_valid(asin: str) -> bool:
    if asin not in _asin_cache:
        return False
    _, cached_at = _asin_cache[asin]
    return datetime.utcnow() - cached_at < timedelta(hours=CACHE_TTL_HOURS)


def _get_cached(asin: str) -> Optional[dict]:
    if _is_cache_valid(asin):
        data, _ = _asin_cache[asin]
        return data
    return None


def _set_cache(asin: str, data: dict):
    _asin_cache[asin] = (data, datetime.utcnow())


# ── فحص ASIN المنشور سابقاً ────────────────────────────────────────────────────
async def is_product_posted_before(asin: str) -> bool:
    """[قديم] يتحقق إن كان ASIN موجود بأي وقت بحالة published/approved.
    استخدم has_published_recently() بدلاً منها للتحقق ضمن نافذة زمنية.
    """
    from backend.config import SessionLocal
    from backend.models import Product
    with SessionLocal() as db:
        existing = (
            db.query(Product)
            .filter(Product.amazon_asin == asin, Product.status.in_(["published", "approved"]))
            .first()
        )
        return existing is not None


def has_published_recently(asin: str, hours: int = 72) -> bool:
    """يتحقق إن كان ASIN قد نُشر بنجاح خلال آخر `hours` ساعة.
    يستخدم PublishLog.published_at المفهرس → سريع جداً.
    """
    from backend.config import SessionLocal
    from backend.models import Product, PublishLog
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    with SessionLocal() as db:
        recent = (
            db.query(PublishLog.id)
            .join(Product, Product.id == PublishLog.product_id)
            .filter(
                Product.amazon_asin == asin,
                PublishLog.success == True,
                PublishLog.published_at >= cutoff,
            )
            .first()
        )
        return recent is not None


def recent_published_asins(hours: int = 72) -> set[str]:
    """يجلب كل الـ ASINs المنشورة بنجاح خلال آخر `hours` ساعة (مرة واحدة)."""
    from backend.config import SessionLocal
    from backend.models import Product, PublishLog
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    with SessionLocal() as db:
        rows = (
            db.query(Product.amazon_asin)
            .join(PublishLog, PublishLog.product_id == Product.id)
            .filter(
                PublishLog.success == True,
                PublishLog.published_at >= cutoff,
                Product.amazon_asin.isnot(None),
            )
            .distinct()
            .all()
        )
        return {r[0] for r in rows if r[0]}


# ── حساب نسبة الخصم ────────────────────────────────────────────────────────────
def calculate_discount(original: float, current: float) -> int:
    if not original or original <= 0:
        return 0
    return max(0, int((original - current) / original * 100))


# ── بناء رابط الإحالة يدوياً ────────────────────────────────────────────────────
def build_affiliate_url(asin: str, associate_tag: str, marketplace: str = "SA") -> str:
    domain = "amazon.sa" if marketplace == "SA" else "amazon.ae"
    return f"https://www.{domain}/dp/{asin}?tag={associate_tag}"


# ── البحث الرئيسي في أمازون ────────────────────────────────────────────────────
async def search_product(query: str) -> Optional[dict]:
    """
    يبحث في Amazon PA API ويُرجع أفضل منتج.
    يعود None إذا لم يجد شيئاً مناسباً أو إذا فشل الاتصال.
    """
    from backend.config import get_api_key, get_setting

    access_key = get_api_key("amazon_access_key")
    secret_key = get_api_key("amazon_secret_key")
    associate_tag = get_api_key("amazon_associate_tag") or get_setting("amazon_associate_tag", "")
    marketplace = get_setting("amazon_marketplace", "SA")

    if not all([access_key, secret_key, associate_tag]):
        logger.info("مفاتيح Amazon PA API غير مكتملة — جاري المحاولة عبر السكرابر/RapidAPI")
        from backend.services.rapidapi_amazon import search_via_rapidapi
        return await search_via_rapidapi(query)

    try:
        result = await asyncio.to_thread(
            _search_amazon_sync, query, access_key, secret_key, associate_tag, marketplace
        )
        if result:
            return result
        logger.info("PA API لم تُرجع نتيجة — جاري المحاولة عبر السكرابر/RapidAPI")
        from backend.services.rapidapi_amazon import search_via_rapidapi
        return await search_via_rapidapi(query)
    except Exception as exc:
        logger.error(f"خطأ في Amazon PA API: {exc} — جاري المحاولة عبر السكرابر/RapidAPI")
        from backend.services.rapidapi_amazon import search_via_rapidapi
        return await search_via_rapidapi(query)


def _search_amazon_sync(
    query: str,
    access_key: str,
    secret_key: str,
    associate_tag: str,
    marketplace: str,
) -> Optional[dict]:
    """استدعاء متزامن لـ Amazon PA API عبر python-amazon-paapi"""
    try:
        from amazon_paapi import AmazonApi

        amz = AmazonApi(
            access_key,
            secret_key,
            associate_tag,
            marketplace,          # "SA" | "AE" | "US" …
            throttling=0.9,
        )

        response = amz.search_items(
            keywords=query,
            search_index="All",
            item_count=10,
        )

        if not response or not response.items_result or not response.items_result.items:
            return None

        for item in response.items_result.items:
            product = _parse_amazon_item(item, associate_tag, marketplace)
            if product:
                return product

        return None

    except ImportError:
        logger.warning("amazon_paapi غير مثبتة")
        return None
    except Exception as exc:
        logger.error(f"خطأ في استدعاء PA API: {exc}")
        return None


def _parse_amazon_item(item, associate_tag: str, marketplace: str) -> Optional[dict]:
    """استخراج بيانات المنتج من استجابة API"""
    try:
        asin = item.asin
        title = ""
        if item.item_info and item.item_info.title:
            title = item.item_info.title.display_value or ""

        price_current = 0.0
        price_original = 0.0
        if item.offers and item.offers.listings:
            listing = item.offers.listings[0]
            if listing.price:
                price_current = listing.price.amount or 0.0
            if listing.saving_basis:
                price_original = listing.saving_basis.amount or price_current

        if price_original == 0.0:
            price_original = price_current

        discount_pct = calculate_discount(price_original, price_current)

        rating = 0.0
        rating_count = 0
        if item.customer_reviews:
            if item.customer_reviews.star_rating:
                rating = item.customer_reviews.star_rating.value or 0.0
            if item.customer_reviews.count:
                rating_count = item.customer_reviews.count or 0

        image_url = ""
        if item.images and item.images.primary and item.images.primary.large:
            image_url = item.images.primary.large.url or ""

        affiliate_url = build_affiliate_url(asin, associate_tag, marketplace)
        domain = "amazon.sa" if marketplace == "SA" else "amazon.ae"
        amazon_url = f"https://www.{domain}/dp/{asin}"

        # فلترة: تقييم > 4.0 وخصم > 20%
        if rating < 4.0 or discount_pct < 20:
            return None

        data = {
            "asin": asin,
            "title": title,
            "affiliate_url": affiliate_url,
            "amazon_url": amazon_url,
            "price_original": price_original,
            "price_current": price_current,
            "discount_pct": discount_pct,
            "rating": rating,
            "rating_count": rating_count,
            "image_url": image_url,
        }
        _set_cache(asin, data)
        return data

    except Exception as exc:
        logger.error(f"خطأ في تحليل بيانات المنتج: {exc}")
        return None



def _mock_product(query: str, associate_tag: str, marketplace: str) -> dict:
    """منتج تجريبي عند غياب مفاتيح API"""
    asin = "B09MOCK1234"
    return {
        "asin": asin,
        "title": f"{query} — [بيانات تجريبية]",
        "affiliate_url": build_affiliate_url(asin, associate_tag, marketplace),
        "amazon_url": f"https://www.amazon.sa/dp/{asin}",
        "price_original": 299.0,
        "price_current": 199.0,
        "discount_pct": 33,
        "rating": 4.5,
        "rating_count": 1234,
        "image_url": "https://via.placeholder.com/400x400.png?text=Product",
    }


# ── اختبار الاتصال ──────────────────────────────────────────────────────────────
async def test_connection() -> dict:
    from backend.config import get_api_key, get_setting
    access_key = get_api_key("amazon_access_key")
    secret_key = get_api_key("amazon_secret_key")
    associate_tag = get_api_key("amazon_associate_tag") or get_setting("amazon_associate_tag", "")

    if not all([access_key, secret_key, associate_tag]):
        return {"success": False, "message": "مفاتيح Amazon غير مكتملة"}

    try:
        result = await search_product("headphones")
        if result:
            return {"success": True, "message": f"متصل ✓ — {result['title'][:50]}"}
        return {"success": False, "message": "لم يُعثر على نتائج"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
