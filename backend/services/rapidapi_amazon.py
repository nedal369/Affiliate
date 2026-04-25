"""
rapidapi_amazon.py — جلب بيانات منتجات أمازون عبر RapidAPI
بديل مجاني/رخيص لـ Amazon PA API

يستخدم: "Real-Time Amazon Data" API
الموقع: https://rapidapi.com/search/amazon
Host: real-time-amazon-data.p.rapidapi.com

الـ endpoint المتاح: /product-offers?asin=ASIN&country=SA
"""
import re
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_RAPIDAPI_HOST = "real-time-amazon-data.p.rapidapi.com"
_BASE_URL      = f"https://{_RAPIDAPI_HOST}"

# خريطة الدول
_COUNTRY_MAP = {"SA": "SA", "AE": "AE", "EG": "EG", "US": "US", "GB": "GB"}

# ASIN تجريبي لاختبار الاتصال
_TEST_ASIN = "B09SM24S8C"


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


def _parse_product_offers_response(data: dict, asin: str, marketplace: str = "SA") -> Optional[dict]:
    """
    يحوّل استجابة /product-offers إلى dict متوافق مع بقية النظام.
    الاستجابة: {"status":"OK","data":{"product_title":..., "product_price":..., ...}}
    """
    try:
        inner = data.get("data") or data
        if not inner:
            return None

        title = (
            inner.get("product_title")
            or inner.get("title", "")
        ).strip()
        if not title:
            return None

        price_curr = _parse_price(
            inner.get("product_price")
            or inner.get("price", "")
        )
        price_orig = _parse_price(
            inner.get("product_original_price")
            or inner.get("original_price", "")
        ) or price_curr

        discount_pct = 0
        if price_orig > 0 and price_curr > 0 and price_orig > price_curr:
            discount_pct = round((1 - price_curr / price_orig) * 100)
        else:
            raw_disc = inner.get("product_discount") or inner.get("discount", "")
            if raw_disc:
                try:
                    discount_pct = int(re.sub(r"[^\d]", "", str(raw_disc)) or 0)
                except ValueError:
                    pass

        rating = 0.0
        raw_rating = inner.get("product_star_rating") or inner.get("stars") or inner.get("rating", "")
        if raw_rating:
            try:
                rating = float(re.sub(r"[^\d.]", "", str(raw_rating)) or 0)
            except ValueError:
                pass

        rating_count = 0
        raw_count = inner.get("product_num_ratings") or inner.get("ratings_total") or inner.get("num_ratings", 0)
        try:
            rating_count = int(str(raw_count).replace(",", "").replace(".", "")) if raw_count else 0
        except ValueError:
            pass

        image_url = (
            inner.get("product_photo")
            or inner.get("product_main_image_url")
            or inner.get("thumbnail")
            or inner.get("image", "")
        )

        domain_map = {"SA": "amazon.sa", "AE": "amazon.ae", "EG": "amazon.eg", "US": "amazon.com", "GB": "amazon.co.uk"}
        domain = domain_map.get(marketplace, "amazon.sa")
        amazon_url = inner.get("product_url") or f"https://www.{domain}/dp/{asin}"

        return {
            "asin":           asin,
            "title":          title,
            "affiliate_url":  _build_affiliate_url(asin, marketplace),
            "amazon_url":     amazon_url,
            "price_original": price_orig,
            "price_current":  price_curr,
            "discount_pct":   discount_pct,
            "rating":         rating,
            "rating_count":   rating_count,
            "image_url":      image_url,
            "source":         "rapidapi",
        }
    except Exception as exc:
        logger.warning(f"خطأ في تحليل استجابة RapidAPI product-offers: {exc}")
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
                    text = await resp.text()
                    logger.warning(f"RapidAPI HTTP {resp.status} for {path}: {text[:200]}")
                    return None
                return await resp.json()
    except Exception as exc:
        logger.error(f"RapidAPI request failed: {exc}")
        return None


async def fetch_product_by_asin(asin: str, marketplace: str = "SA") -> Optional[dict]:
    """
    يجلب بيانات المنتج بـ ASIN.
    يختار المصدر بناءً على إعداد amazon_data_source:
      "scraper" → السكرابر المجاني دائماً
      "auto"    → السكرابر أولاً، RapidAPI كـ fallback
      "rapidapi" → RapidAPI مباشرة
    """
    from backend.config import get_setting
    source = get_setting("amazon_data_source", "scraper")

    if source in ("scraper", "auto"):
        try:
            from backend.services.amazon_scraper import get_product_by_asin
            result = await get_product_by_asin(asin, marketplace)
            if result:
                logger.info(f"Scraper ASIN {asin}: {result['title'][:50]}")
                return result
        except Exception as exc:
            logger.warning(f"Scraper فشل لـ ASIN {asin}: {exc}")
        if source == "scraper":
            return None

    # RapidAPI fallback
    country = _COUNTRY_MAP.get(marketplace, "SA")
    data = await _rapidapi_get("/product-offers", {
        "asin":    asin,
        "country": country,
        "limit":   "1",
        "page":    "1",
    })

    if not data:
        return None

    product = _parse_product_offers_response(data, asin, marketplace)
    if product:
        logger.info(
            f"RapidAPI product-offers — ASIN {asin}: "
            f"{product['title'][:50]} — خصم {product['discount_pct']}%"
        )
    return product


async def search_via_rapidapi(query: str) -> Optional[dict]:
    """
    يبحث عن منتج في أمازون.
    يختار المصدر بناءً على إعداد amazon_data_source.
    """
    from backend.config import get_setting
    marketplace = get_setting("amazon_marketplace", "SA")
    source = get_setting("amazon_data_source", "scraper")

    if source in ("scraper", "auto"):
        try:
            from backend.services.amazon_scraper import search_amazon
            results = await search_amazon(query, marketplace, num_results=10)
            if results:
                # أعد أفضل نتيجة (الأعلى خصماً وتقييماً)
                best = max(results, key=lambda r: r.get("discount_pct", 0) * 0.5 + r.get("rating", 0) * 10)
                return best
        except Exception as exc:
            logger.warning(f"Scraper search فشل لـ '{query}': {exc}")
        if source == "scraper":
            return None

    # إذا كانت query تبدو كـ ASIN → جرّب /product-offers مباشرة
    asin_match = re.fullmatch(r"[A-Z0-9]{10}", query.strip().upper())
    if asin_match:
        return await fetch_product_by_asin(query.strip().upper(), marketplace)

    # /products/search قد لا يكون متاحاً في جميع خطط RapidAPI
    data = await _rapidapi_get("/products/search", {
        "query":       query,
        "country":     _COUNTRY_MAP.get(marketplace, "SA"),
        "sort_by":     "RELEVANCE",
        "page":        "1",
        "num_results": "10",
    })

    if not data:
        logger.info("RapidAPI /products/search غير متاح — يتطلب اشتراكاً أعلى")
        return None

    min_discount = get_setting("min_discount_pct", 20)
    min_rating   = get_setting("min_rating", 3.5)

    products = (
        data.get("data", {}).get("products")
        or data.get("products")
        or data.get("data", [])
        or []
    )

    for item in products:
        asin = item.get("asin") or item.get("product_asin", "")
        if not asin:
            continue
        product = _parse_product_offers_response({"data": item}, asin, marketplace)
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
    يحاول جلب قائمة المنتجات الأكثر مبيعاً.
    قد لا يكون متاحاً في جميع خطط RapidAPI.
    """
    from backend.config import get_setting
    marketplace = get_setting("amazon_marketplace", "SA")
    min_discount = get_setting("min_discount_pct", 20)
    min_rating   = get_setting("min_rating", 3.5)

    data = await _rapidapi_get("/products/bestsellers", {
        "country":  _COUNTRY_MAP.get(marketplace, "SA"),
        "category": "aps",
        "page":     "1",
    })

    if not data:
        logger.info("RapidAPI /products/bestsellers غير متاح في هذا الاشتراك")
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
        asin = item.get("asin") or item.get("product_asin", "")
        if not asin:
            continue
        product = _parse_product_offers_response({"data": item}, asin, marketplace)
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
    """اختبار الاتصال بـ RapidAPI باستخدام /product-offers مع ASIN تجريبي"""
    from backend.config import get_api_key
    if not get_api_key("rapidapi_key"):
        return {"success": False, "message": "مفتاح RapidAPI غير موجود"}
    try:
        product = await fetch_product_by_asin(_TEST_ASIN)
        if product:
            title = product.get("title", "منتج")[:40]
            price = product.get("price_current", 0)
            return {"success": True, "message": f"متصل ✓ — {title} ({price})"}
        return {"success": False, "message": "لا توجد استجابة من RapidAPI — تحقق من المفتاح أو النطاق المسموح به"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
