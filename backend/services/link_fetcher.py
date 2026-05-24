"""
link_fetcher.py — جلب بيانات المنتج تلقائياً من رابط الإحالة

ترتيب المحاولات:
  1. Amazon PA API (إذا كانت المفاتيح مضبوطة) — أفضل جودة
  2. JSON-LD structured data في الصفحة — موثوق لمعظم المتاجر
  3. Open Graph meta tags — يعمل على غالبية المواقع
  4. Amazon HTML selectors — fallback خاص بأمازون
"""
import re
import json
import asyncio
import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import aiohttp

logger = logging.getLogger(__name__)

# ── SSRF Protection ──────────────────────────────────────────────────────────
# نطاقات أمازون المسموح بها فقط
_ALLOWED_HOST_PATTERN = re.compile(
    r"(?:^|\.)(amazon\.(?:sa|ae|com|co\.uk|de|fr|com\.au|com\.tr|in|it|es|nl|pl|se)|amzn\.to|a\.co)$",
    re.IGNORECASE,
)


def _check_url_fast(url: str) -> str:
    """تحقق سريع بدون DNS: بروتوكول، hostname، IP literals، وقائمة النطاقات.
    يرجع hostname عند النجاح، أو يرفع ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"بروتوكول غير مسموح: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("hostname مفقود في الرابط")

    # ① إذا كان hostname هو IP صريح، تأكد أنه ليس داخلياً
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise ValueError(f"IP داخلي/محجوز غير مسموح: {hostname}")
        raise ValueError(f"الوصول المباشر لـ IP غير مسموح: {hostname}")
    except ValueError as exc:
        if "is not a valid IPAddress" in str(exc) or "does not appear" in str(exc):
            pass
        elif hostname not in str(exc):
            raise

    # ② تحقق من النطاق
    if not _ALLOWED_HOST_PATTERN.search(hostname.lower()):
        raise ValueError(f"النطاق غير مسموح: {hostname} — فقط نطاقات أمازون مدعومة")

    return hostname


async def _validate_safe_url(url: str) -> None:
    """SSRF check كامل: بروتوكول + hostname + IP literals + قائمة النطاقات + DNS async.
    يرفع ValueError إذا كان الرابط غير آمن.
    """
    hostname = _check_url_fast(url)

    # ③ DNS async — لا يحجب الـ event loop (socket.getaddrinfo يحجب Thread)
    try:
        resolved_ips = await asyncio.to_thread(
            lambda: {info[4][0] for info in socket.getaddrinfo(hostname, None)}
        )
        for ip_str in resolved_ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
                    raise ValueError(f"النطاق {hostname} يشير لـ IP داخلي: {ip_str}")
            except ValueError as exc:
                if "is not a valid" not in str(exc):
                    raise
    except (socket.gaierror, OSError):
        pass

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}


# ── استخراج ASIN من رابط أمازون ─────────────────────────────────────────────────
def extract_asin(url: str) -> Optional[str]:
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"[?&]asin=([A-Z0-9]{10})",
        r"/([A-Z0-9]{10})(?:/|\?|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            candidate = m.group(1)
            # تأكد أنه ASIN فعلي (عادةً B0 أو رقمي 10 خانات)
            if len(candidate) == 10:
                return candidate
    return None


def _is_amazon_url(url: str) -> bool:
    return "amazon." in urlparse(url).netloc


# ── Amazon PA API (get_items by ASIN) ────────────────────────────────────────────
async def _fetch_via_paapi(asin: str) -> Optional[dict]:
    """يجلب بيانات المنتج عبر Amazon PA API باستخدام ASIN"""
    try:
        from backend.config import get_api_key, get_setting
        access_key = get_api_key("amazon_access_key")
        secret_key  = get_api_key("amazon_secret_key")
        assoc_tag   = get_api_key("amazon_associate_tag")
        if not all([access_key, secret_key, assoc_tag]):
            return None

        marketplace = get_setting("amazon_marketplace", "SA")

        def _sync_call():
            from amazon_paapi import AmazonApi
            amz = AmazonApi(access_key, secret_key, assoc_tag, marketplace, throttling=0.9)
            return amz.get_items([asin])

        response = await asyncio.to_thread(_sync_call)
        if not response or not response.items_result or not response.items_result.items:
            return None

        item = response.items_result.items[0]

        # العنوان
        title = ""
        if item.item_info and item.item_info.title:
            title = item.item_info.title.display_value or ""

        # السعر
        price_curr = 0.0
        price_orig = 0.0
        if item.offers and item.offers.listings:
            listing = item.offers.listings[0]
            if listing.price:
                price_curr = listing.price.amount or 0.0
            if listing.saving_basis:
                price_orig = listing.saving_basis.amount or 0.0
            elif price_curr and listing.price and getattr(listing.price, "savings", None):
                savings = listing.price.savings.amount or 0
                price_orig = price_curr + savings
        if price_orig == 0.0:
            price_orig = price_curr

        discount_pct = 0
        if price_orig > 0 and price_curr > 0 and price_orig > price_curr:
            discount_pct = round((1 - price_curr / price_orig) * 100)

        # التقييم
        rating = 0.0
        rating_count = 0
        if item.customer_reviews:
            if item.customer_reviews.star_rating:
                rating = float(item.customer_reviews.star_rating.value or 0)
            if item.customer_reviews.count:
                rating_count = int(item.customer_reviews.count or 0)

        # الصورة
        image_url = None
        if item.images and item.images.primary and item.images.primary.large:
            image_url = item.images.primary.large.url or None

        logger.info(f"PA API جلب ASIN {asin}: {title[:50]}")
        return {
            "title":        title,
            "price_current": price_curr,
            "price_original": price_orig,
            "discount_pct": discount_pct,
            "rating":       rating,
            "rating_count": rating_count,
            "image_url":    image_url,
            "source":       "paapi",
        }

    except Exception as exc:
        logger.warning(f"PA API فشل لـ {asin}: {exc}")
        return None


# ── جلب HTML الصفحة ──────────────────────────────────────────────────────────────
def _looks_blocked(html: str) -> bool:
    if not html or len(html) < 5000:
        return True
    lower = html.lower()
    return any(x in lower for x in (
        "robot check", "validatecaptcha", "captcha", "api-services-support",
        "automated access", "unusual traffic", "verify you are human",
    ))


async def _fetch_html(url: str) -> Optional[str]:
    """
    Cascade: aiohttp → Scrapling fast → Scrapling stealth.
    الـ caller (fetch_product_data) تحقق بالفعل من الرابط (SSRF whitelist).
    """
    # ── الطبقة ①: aiohttp ───────────────────────────────────────────────
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(headers=_HEADERS) as session:
            async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
                final_url = str(resp.url)
                if final_url.rstrip("/") != url.rstrip("/"):
                    try:
                        await _validate_safe_url(final_url)
                    except ValueError as exc:
                        logger.warning(f"redirect إلى نطاق غير مسموح: {exc}")
                        return None
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    if not _looks_blocked(html):
                        return html
                    logger.info(f"link_fetcher: aiohttp يبدو محظوراً — أحاول Scrapling")
                else:
                    logger.info(f"link_fetcher: HTTP {resp.status} — أحاول Scrapling")
    except Exception as exc:
        logger.warning(f"link_fetcher: aiohttp فشل ({exc}) — أحاول Scrapling")

    # ── الطبقتان ② و ③: Scrapling fast → stealth (داخل stealth_fetch mode=auto) ──
    try:
        from backend.services.stealth_fetcher import stealth_fetch
        html, status = await stealth_fetch(url, mode="auto", timeout=30.0)
        if html and not _looks_blocked(html):
            logger.info(f"link_fetcher: ✓ Scrapling نجح ({len(html)} bytes)")
            return html
    except Exception as exc:
        logger.warning(f"link_fetcher: Scrapling فشل: {exc}")

    logger.warning(f"link_fetcher: كل الطبقات فشلت — {url}")
    return None


# ── استخراج JSON-LD ──────────────────────────────────────────────────────────────
def _parse_json_ld(html: str) -> dict:
    result = {}
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    )
    for block in blocks:
        try:
            data = json.loads(block.strip())
            if isinstance(data, list):
                data = next((d for d in data if isinstance(d, dict) and d.get("@type") == "Product"), {})
            if not isinstance(data, dict) or data.get("@type") != "Product":
                continue

            if data.get("name"):
                result["title"] = str(data["name"]).strip()

            # صورة
            img = data.get("image")
            if img:
                if isinstance(img, list):
                    img = img[0]
                if isinstance(img, dict):
                    img = img.get("url", "")
                if img:
                    result["image_url"] = str(img)

            # سعر من offers
            offers = data.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                price_str = offers.get("price") or offers.get("lowPrice") or ""
                if price_str:
                    try:
                        result["price_current"] = float(str(price_str).replace(",", "").strip())
                    except ValueError:
                        pass
                currency = offers.get("priceCurrency", "")
                if currency:
                    result["currency"] = currency

            # تقييم
            agg = data.get("aggregateRating") or {}
            if isinstance(agg, dict):
                rv = agg.get("ratingValue")
                rc = agg.get("reviewCount") or agg.get("ratingCount")
                if rv:
                    try:
                        result["rating"] = float(rv)
                    except ValueError:
                        pass
                if rc:
                    try:
                        result["rating_count"] = int(str(rc).replace(",", ""))
                    except ValueError:
                        pass

            if result.get("title"):
                break

        except (json.JSONDecodeError, Exception):
            continue

    return result


# ── استخراج Open Graph ───────────────────────────────────────────────────────────
def _parse_open_graph(html: str) -> dict:
    result = {}
    # نستخرج content بعد property أو name
    patterns = {
        "title":         r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\'<]+)["\']',
        "image_url":     r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\'<]+)["\']',
        "price_current": r'<meta[^>]+(?:property|name)=["\'](?:og:price:amount|product:price:amount)["\'][^>]+content=["\']([^"\'<]+)["\']',
        "description":   r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\'<]{10,})["\']',
    }
    # بعض الصفحات تضع content قبل property
    rev_patterns = {
        "title":     r'<meta[^>]+content=["\']([^"\'<]+)["\'][^>]+(?:property|name)=["\']og:title["\']',
        "image_url": r'<meta[^>]+content=["\']([^"\'<]+)["\'][^>]+property=["\']og:image(?::url)?["\']',
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if key == "price_current":
                try:
                    result[key] = float(val.replace(",", ""))
                except ValueError:
                    pass
            else:
                result[key] = val
    for key, pattern in rev_patterns.items():
        if key not in result:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                result[key] = m.group(1).strip()
    return result


# ── استخراج خاص بأمازون ─────────────────────────────────────────────────────────
def _parse_amazon_html(html: str) -> dict:
    result = {}

    # العنوان
    m = re.search(r'id=["\']productTitle["\'][^>]*>\s*(.*?)\s*</span>', html, re.DOTALL)
    if m:
        result["title"] = re.sub(r'\s+', ' ', m.group(1)).strip()

    # السعر الحالي: whole.fraction
    m_whole = re.search(r'class=["\'][^"\']*a-price-whole[^"\']*["\'][^>]*>\s*([\d,.]+)', html)
    m_frac  = re.search(r'class=["\'][^"\']*a-price-fraction[^"\']*["\'][^>]*>\s*(\d{2})', html)
    if m_whole:
        try:
            whole = m_whole.group(1).replace(",", "").replace(".", "")
            frac  = m_frac.group(1) if m_frac else "00"
            result["price_current"] = float(f"{whole}.{frac}")
        except ValueError:
            pass

    # الصورة
    for pattern in [
        r'id=["\']landingImage["\'][^>]+src=["\']([^"\']+)["\']',
        r'id=["\']imgTagWrappingLink["\'][^>]*>.*?<img[^>]+src=["\']([^"\']+)["\']',
        r'data-old-hires=["\']([^"\']+)["\']',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m and "http" in m.group(1):
            result["image_url"] = m.group(1)
            break

    # التقييم
    m = re.search(r'([\d.]+)\s+(?:out of|من)\s+5', html)
    if m:
        try:
            result["rating"] = float(m.group(1))
        except ValueError:
            pass

    # عدد التقييمات
    m = re.search(r'([\d,]+)\s+(?:global ratings?|ratings?|تقييم)', html, re.IGNORECASE)
    if m:
        try:
            result["rating_count"] = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    return result


# ── الدالة الرئيسية ──────────────────────────────────────────────────────────────
async def fetch_product_data(url: str) -> dict:
    """
    تجلب بيانات المنتج تلقائياً من الرابط.

    الإرجاع: dict يحتوي على:
        title, price_current, price_original, discount_pct,
        rating, rating_count, image_url, amazon_asin
    """
    # SSRF Protection — async لعدم حجب الـ event loop أثناء DNS
    await _validate_safe_url(url)

    data: dict = {
        "title":          None,
        "price_current":  0.0,
        "price_original": 0.0,
        "discount_pct":   0,
        "rating":         0.0,
        "rating_count":   0,
        "image_url":      None,
        "amazon_asin":    None,
        "fetch_source":   "scrape",
    }

    # ① استخرج ASIN إن وُجد
    asin = extract_asin(url)
    if asin:
        data["amazon_asin"] = asin

    # ② إذا كان رابط أمازون وعندنا ASIN → جرّب السكرابر/RapidAPI أولاً (أسرع وأدق)
    if asin and _is_amazon_url(url):
        try:
            from backend.config import get_setting
            from backend.services.rapidapi_amazon import fetch_product_by_asin
            marketplace = get_setting("amazon_marketplace", "SA")
            rapid_data = await fetch_product_by_asin(asin, marketplace)
            if rapid_data and rapid_data.get("title"):
                data["title"]          = rapid_data.get("title")
                data["price_current"]  = rapid_data.get("price_current", 0.0)
                data["price_original"] = rapid_data.get("price_original", 0.0)
                data["discount_pct"]   = rapid_data.get("discount_pct", 0)
                data["rating"]         = rapid_data.get("rating", 0.0)
                data["rating_count"]   = rapid_data.get("rating_count", 0)
                if rapid_data.get("image_url"):
                    data["image_url"]  = rapid_data["image_url"]
                src = rapid_data.get("source", "scraper")
                data["fetch_source"]   = src
                logger.info(f"link_fetcher: {src} نجح لـ ASIN {asin}")
                return data
        except Exception as exc:
            logger.warning(f"link_fetcher: جلب ASIN فشل لـ {asin}: {exc}")

    # ③ اجلب HTML الصفحة
    html = await _fetch_html(url)
    if not html:
        return data

    # ④ JSON-LD (الأكثر موثوقية)
    ld = _parse_json_ld(html)
    _merge(data, ld)

    # ⑤ Open Graph
    og = _parse_open_graph(html)
    _merge(data, og)

    # ⑥ Amazon-specific fallback
    if _is_amazon_url(url):
        amz = _parse_amazon_html(html)
        _merge(data, amz)

    # حساب الخصم إذا لم يكن محسوباً
    if data["price_current"] and data["price_original"] and data["price_original"] > data["price_current"]:
        data["discount_pct"] = round((1 - data["price_current"] / data["price_original"]) * 100)

    logger.info(
        f"fetch_product_data [{data['fetch_source']}]: "
        f"title={str(data['title'])[:40]}, price={data['price_current']}"
    )
    return data


def resolve_affiliate_url(raw_url: str) -> str:
    """
    يُحوّل أي رابط أمازون عادي إلى رابط إحالة:
    - إذا كان الرابط يحتوي فعلاً على tag= → يُرجعه كما هو
    - إذا كان رابط أمازون بدون tag= → يستخرج ASIN ويبني رابط إحالة
      باستخدام المعرّف المحفوظ في الإعدادات (افتراضي: nedal01-21)
    - أي رابط آخر → يُرجعه كما هو
    """
    parsed = urlparse(raw_url)
    params = parse_qs(parsed.query)

    # الرابط يحتوي بالفعل على معرّف إحالة
    if "tag" in params:
        return raw_url

    # رابط أمازون بدون معرّف → أضف المعرّف
    if _is_amazon_url(raw_url):
        asin = extract_asin(raw_url)
        if asin:
            from backend.config import get_api_key, get_setting
            tag = (
                get_api_key("amazon_associate_tag")
                or get_setting("amazon_associate_tag", "nedal01-21")
            )
            marketplace = get_setting("amazon_marketplace", "SA")
            domain_map = {"SA": "amazon.sa", "AE": "amazon.ae", "EG": "amazon.eg"}
            domain = domain_map.get(marketplace, "amazon.sa")
            return f"https://www.{domain}/dp/{asin}?tag={tag}"

    # ليس رابط أمازون → أرجعه كما هو
    return raw_url


def _merge(base: dict, extra: dict):
    """يدمج extra في base فقط للمفاتيح التي قيمتها None أو 0"""
    for k, v in extra.items():
        if v is None or v == "" or v == 0 or v == 0.0:
            continue
        current = base.get(k)
        if current is None or current == "" or current == 0 or current == 0.0:
            base[k] = v
