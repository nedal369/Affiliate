"""
rapidapi_amazon.py — جلب بيانات منتجات أمازون عبر RapidAPI
بديل مجاني/رخيص لـ Amazon PA API

يستخدم: "Real-Time Amazon Data" API
الموقع: https://rapidapi.com/search/amazon
Host: real-time-amazon-data.p.rapidapi.com
"""
import re
import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_RAPIDAPI_HOST = "real-time-amazon-data.p.rapidapi.com"
_BASE_URL      = f"https://{_RAPIDAPI_HOST}"

# خريطة الدول
_COUNTRY_MAP = {"SA": "SA", "AE": "AE", "EG": "EG", "US": "US", "GB": "GB"}


# ── مساعدات ──────────────────────────────────────────────────────────────────────

def _parse_price(price_str) -> float:
    """تحويل 'SAR 299.00' أو '299٫00' أو '$29.99' إلى float"""
    if not price_str:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", str(price_str).replace(",", "."))
    # إذا تعددت النقاط احتفظ بالأخيرة فقط
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def _build_affiliate_url(asin: str, marketplace: str = "SA") -> str:
    from backend.config import get_api_key, get_setting
    tag = (
        get_api_key("amazon_associate_tag")
        or get_setting("amazon_associate_tag", "nedal01-21")
    )
    domain_map = {"SA": "amazon.sa", "AE": "amazon.ae", "EG": "amazon.eg", "US": "amazon.com", "GB": "amazon.co.uk"}
    domain = domain_map.get(marketplace, "amazon.sa")
    return f"https://www.{domain}/dp/{asin}?tag={tag}"


def _parse_product(item: dict, marketplace: str = "SA") -> Optional[dict]:
    """يحوّل استجابة RapidAPI إلى dict متوافق مع بقية النظام"""
    try:
        asin = item.get("asin") or item.get("product_asin", "")
        if not asin:
            return None

        title = (
            item.get("product_title")
            or item.get("title")
            or item.get("product_name", "")
        ).strip()
        if not title:
            return None

        price_curr = _parse_price(
            item.get("product_price")
            or item.get("price")
            or item.get("current_price", "")
        )
        price_orig = _parse_price(
            item.get("product_original_price")
            or item.get("original_price")
            or item.get("list_price", "")
        ) or price_curr

        discount_pct = 0
        if price_orig > 0 and price_curr > 0 and price_orig > price_curr:
            discount_pct = round((1 - price_curr / price_orig) * 100)
        else:
            # بعض الاستجابات تحتوي على نسبة الخصم مباشرة
            raw_disc = item.get("product_discount") or item.get("discount", "")
            if raw_disc:
                try:
                    discount_pct = int(re.sub(r"[^\d]", "", str(raw_disc)) or 0)
                except ValueError:
                    pass

        rating = 0.0
        raw_rating = item.get("product_star_rating") or item.get("stars") or item.get("rating", "")
        if raw_rating:
            try:
                rating = float(re.sub(r"[^\d.]", "", str(raw_rating)) or 0)
            except ValueError:
                pass

        rating_count = 0
        raw_count = item.get("product_num_ratings") or item.get("ratings_total") or item.get("num_ratings", 0)
        try:
            rating_count = int(str(raw_count).replace(",", "").replace(".", "")) if raw_count else 0
        except ValueError:
            pass

        image_url = (
            item.get("product_photo")
            or item.get("product_main_image_url")
            or item.get("thumbnail")
            or item.get("image", "")
        )

        domain_map = {"SA": "amazon.sa", "AE": "amazon.ae", "EG": "amazon.eg", "US": "amazon.com", "GB": "amazon.co.uk"}
        domain = domain_map.get(marketplace, "amazon.sa")
        amazon_url = item.get("product_url") or f"https://www.{domain}/dp/{asin}"

        return {
            "asin":          asin,
            "title":         title,
            "affiliate_url": _build_affiliate_url(asin, marketplace),
            "amazon_url":    amazon_url,
            "price_original": price_orig,
            "price_current":  price_curr,
            "discount_pct":   discount_pct,
            "rating":         rating,
            "rating_count":   rating_count,
            "image_url":      image_url,
        }
    except Exception as exc:
        logger.warning(f"خطأ في تحليل منتج RapidAPI: {exc}")
        return None


# ── الاستدعاءات الرئيسية ──────────────────────────────────────────────────────────

async def _rapidapi_get(path: str, params: dict) -> Optional[dict]:
    """HTTP GET لـ RapidAPI مع المعالجة الموحّدة للأخطاء"""
    from backend.config import get_api_key
    api_key = get_api_key("rapidapi_key")
    if not api_key:
        logger.warning("مفتاح RapidAPI غير موجود")
        return None

    headers = {
        "X-RapidAPI-Key":  api_key,
        "X-RapidAPI-Host": _RAPIDAPI_HOST,
    }
    url = f"{_BASE_URL}{path}"
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=timeout) as resp:
                if resp.status != 200:
                    logger.warning(f"RapidAPI HTTP {resp.status} for {path}")
                    return None
                return await resp.json()
    except Exception as exc:
        logger.error(f"RapidAPI request failed: {exc}")
        return None


async def search_via_rapidapi(query: str) -> Optional[dict]:
    """
    يبحث عن منتج في أمازون عبر RapidAPI.
    يُرجع أفضل منتج يطابق معايير الخصم والتقييم.
    """
    from backend.config import get_setting
    marketplace = get_setting("amazon_marketplace", "SA")
    min_discount = get_setting("min_discount_pct", 20)
    min_rating   = get_setting("min_rating", 3.5)

    data = await _rapidapi_get("/products/search", {
        "query":      query,
        "country":    _COUNTRY_MAP.get(marketplace, "SA"),
        "sort_by":    "RELEVANCE",
        "page":       "1",
        "num_results": "10",
    })

    if not data:
        return None

    # استخرج قائمة المنتجات من أي هيكل استجابة
    products = (
        data.get("data", {}).get("products")
        or data.get("products")
        or data.get("data", [])
        or []
    )

    for item in products:
        product = _parse_product(item, marketplace)
        if not product:
            continue
        if product["discount_pct"] < min_discount:
            continue
        if product["rating"] < min_rating:
            continue
        logger.info(f"RapidAPI وجد: {product['title'][:50]} — خصم {product['discount_pct']}%")
        return product

    logger.info(f"RapidAPI: لا توجد منتجات مناسبة لـ '{query}'")
    return None


async def fetch_bestsellers_via_rapidapi() -> list[dict]:
    """
    يجلب قائمة المنتجات الأكثر مبيعاً (للجدولة التلقائية).
    """
    from backend.config import get_setting
    marketplace = get_setting("amazon_marketplace", "SA")
    min_discount = get_setting("min_discount_pct", 20)
    min_rating   = get_setting("min_rating", 3.5)

    data = await _rapidapi_get("/products/bestsellers", {
        "country":   _COUNTRY_MAP.get(marketplace, "SA"),
        "category":  "aps",     # جميع الأقسام
        "page":      "1",
    })

    if not data:
        return []

    products_raw = (
        data.get("data", {}).get("bestsellers")
        or data.get("data", {}).get("products")
        or data.get("bestsellers")
        or data.get("products")
        or []
    )

    results = []
    for item in products_raw:
        product = _parse_product(item, marketplace)
        if not product:
            continue
        if product["discount_pct"] < min_discount:
            continue
        if product["rating"] < min_rating:
            continue
        results.append(product)

    logger.info(f"RapidAPI bestsellers: {len(results)} منتج مناسب")
    return results


async def test_rapidapi_connection() -> dict:
    """اختبار الاتصال بـ RapidAPI"""
    from backend.config import get_api_key
    if not get_api_key("rapidapi_key"):
        return {"success": False, "message": "مفتاح RapidAPI غير موجود"}
    try:
        from backend.config import get_setting
        marketplace = get_setting("amazon_marketplace", "SA")
        data = await _rapidapi_get("/products/search", {
            "query":   "headphones",
            "country": _COUNTRY_MAP.get(marketplace, "SA"),
            "page":    "1",
            "num_results": "1",
        })
        if data:
            products = (
                data.get("data", {}).get("products")
                or data.get("products")
                or []
            )
            if products:
                title = products[0].get("product_title", "منتج")[:40]
                return {"success": True, "message": f"متصل ✓ — مثال: {title}"}
            return {"success": True, "message": "متصل ✓ — RapidAPI جاهز"}
        return {"success": False, "message": "لا توجد استجابة من RapidAPI"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
