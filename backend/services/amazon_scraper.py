"""
amazon_scraper.py — سكرابر أمازون مجاني (بديل RapidAPI)

يستخدم HTML كشط مباشر من amazon.sa/ae/com بدون API مدفوعة.
يدعم: البحث، جلب منتج بـ ASIN، الأكثر مبيعاً.

مكافحة Anti-Bot:
- دوران User-Agents
- Cookie jar مشترك
- Semaphore (2 طلب متوازٍ)
- Exponential backoff عند 429/503
- تأخير عشوائي بين الطلبات
"""
import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── User-Agents ──────────────────────────────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

# ── Domain map ────────────────────────────────────────────────────────────────────
_DOMAIN_MAP = {
    "SA": "www.amazon.sa",
    "AE": "www.amazon.ae",
    "EG": "www.amazon.eg",
    "US": "www.amazon.com",
    "GB": "www.amazon.co.uk",
}

# ── Bestseller slug overrides per marketplace ─────────────────────────────────────
_SA_BESTSELLER_SLUG_MAP = {
    "smart-home": "amazon-devices",
    "wireless": "wireless",
    "grocery": "food-and-grocery",
    "kitchen": "kitchen",
    "home": "home",
    "beauty": "beauty",
    "electronics": "electronics",
    "videogames": "videogames",
    "toys": "toys",
    "baby-products": "baby-products",
    "sports": "sports",
    "automotive": "automotive",
    "office-products": "office-products",
    "aps": "aps",
}

# ── Rate-limiting state ───────────────────────────────────────────────────────────
_semaphore = asyncio.Semaphore(2)
_blocked_until: float = 0.0   # epoch seconds

# ── Per-domain aiohttp sessions (reuse cookies) ───────────────────────────────────
_sessions: dict[str, aiohttp.ClientSession] = {}


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }


async def _get_session(domain: str) -> aiohttp.ClientSession:
    if domain not in _sessions or _sessions[domain].closed:
        jar = aiohttp.CookieJar(unsafe=True)
        _sessions[domain] = aiohttp.ClientSession(
            cookie_jar=jar,
            timeout=aiohttp.ClientTimeout(total=25),
        )
    return _sessions[domain]


def _is_blocked(html: str, status: int) -> bool:
    if status in (403, 503):
        return True
    if len(html) < 5000:
        return True
    lower = html.lower()
    if any(x in lower for x in ["robot check", "validatecaptcha", "captcha", "api-services-support"]):
        return True
    return False


async def _fetch(url: str, retries: int = 3) -> tuple[str, int]:
    """جلب URL مع retry/backoff. يُرجع (html, status)."""
    global _blocked_until

    domain = re.search(r"https?://([^/]+)", url)
    domain_key = domain.group(1) if domain else "www.amazon.sa"
    session = await _get_session(domain_key)

    for attempt in range(retries):
        # انتظر إذا كنا محظورين
        now = time.monotonic()
        if _blocked_until > now:
            wait = _blocked_until - now
            logger.warning(f"Scraper: محظور — انتظار {wait:.0f} ثانية")
            await asyncio.sleep(wait)

        # تأخير عشوائي بين الطلبات
        if attempt > 0:
            delay = random.uniform(0.8, 2.5) * (2 ** attempt)
            await asyncio.sleep(delay)
        elif attempt == 0 and _blocked_until == 0.0:
            await asyncio.sleep(random.uniform(0.3, 1.0))

        try:
            async with session.get(url, headers=_get_headers(), allow_redirects=True) as resp:
                status = resp.status
                html = await resp.text(errors="replace")

                if _is_blocked(html, status):
                    logger.warning(f"Scraper: حظر مكتشف (status={status}) — backoff 60s")
                    _blocked_until = time.monotonic() + 60
                    if attempt < retries - 1:
                        continue
                    return "", status

                return html, status

        except asyncio.TimeoutError:
            logger.warning(f"Scraper: timeout {url} (محاولة {attempt + 1})")
        except Exception as exc:
            logger.warning(f"Scraper: خطأ {url}: {exc} (محاولة {attempt + 1})")

    return "", 0


# ── Price parser ──────────────────────────────────────────────────────────────────
def _parse_price(text: str) -> float:
    if not text:
        return 0.0
    # تحويل الأرقام العربية
    arabic = "٠١٢٣٤٥٦٧٨٩"
    for i, ch in enumerate(arabic):
        text = text.replace(ch, str(i))
    # فواصل عربية U+066B و U+066C
    text = text.replace("٫", ".").replace("٬", ",")
    # إزالة كل ما ليس رقماً أو نقطة
    cleaned = re.sub(r"[^\d.,]", "", text).replace(",", "")
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


# ── affiliate URL builder ─────────────────────────────────────────────────────────
def _affiliate_url(asin: str, marketplace: str = "SA") -> str:
    from backend.config import get_api_key, get_setting
    tag = get_api_key("amazon_associate_tag") or get_setting("amazon_associate_tag", "nedal01-21")
    domain = _DOMAIN_MAP.get(marketplace, "www.amazon.sa")
    return f"https://{domain}/dp/{asin}?tag={tag}"


# ── SQLite cache helpers ──────────────────────────────────────────────────────────
def _cache_get(key: str, ttl_hours: float) -> Optional[object]:
    try:
        from backend.config import SessionLocal
        from backend.models import ScraperCache
        with SessionLocal() as db:
            row = db.get(ScraperCache, key)
            if row is None:
                return None
            age = datetime.utcnow() - row.fetched_at
            if age > timedelta(hours=ttl_hours):
                return None
            return json.loads(row.payload)
    except Exception:
        return None


def _cache_set(key: str, payload: object):
    try:
        from backend.config import SessionLocal
        from backend.models import ScraperCache
        with SessionLocal() as db:
            row = db.get(ScraperCache, key)
            serialized = json.dumps(payload, ensure_ascii=False)
            if row:
                row.payload = serialized
                row.fetched_at = datetime.utcnow()
            else:
                db.add(ScraperCache(
                    cache_key=key,
                    payload=serialized,
                    fetched_at=datetime.utcnow(),
                ))
            db.commit()
    except Exception as exc:
        logger.debug(f"Cache set failed: {exc}")


def _cache_get_stale(key: str) -> Optional[object]:
    """إرجاع أي قيمة محفوظة بغض النظر عن العمر (للـ stale fallback)"""
    try:
        from backend.config import SessionLocal
        from backend.models import ScraperCache
        with SessionLocal() as db:
            row = db.get(ScraperCache, key)
            if row is None:
                return None
            return json.loads(row.payload)
    except Exception:
        return None


# ── JSON-LD parser ────────────────────────────────────────────────────────────────
def _parse_json_ld(html: str) -> dict:
    result = {}
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
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

            img = data.get("image")
            if img:
                if isinstance(img, list):
                    img = img[0]
                if isinstance(img, dict):
                    img = img.get("url", "")
                if img and "http" in str(img):
                    result["image_url"] = str(img)

            offers = data.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                price_str = offers.get("price") or offers.get("lowPrice") or ""
                if price_str:
                    p = _parse_price(str(price_str))
                    if p > 0:
                        result["price_current"] = p

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
        except Exception:
            continue
    return result


# ── Product page CSS selectors ────────────────────────────────────────────────────
def _parse_product_html(html: str) -> dict:
    result = {}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # العنوان
    title_el = soup.select_one("#productTitle")
    if title_el:
        result["title"] = title_el.get_text(" ", strip=True)

    # السعر الحالي: نجرّب عدة selectors
    for sel in [
        ".priceToPay .a-offscreen",
        "#corePrice_feature_div .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price:not(.a-text-price) .a-offscreen",
    ]:
        el = soup.select_one(sel)
        if el:
            p = _parse_price(el.get_text())
            if p > 0:
                result["price_current"] = p
                break

    # السعر الأصلي (المشطوب)
    for sel in [
        ".basisPrice .a-offscreen",
        ".a-text-price .a-offscreen",
        "[data-a-strike='true'] .a-offscreen",
        "#priceblock_ourprice ~ .a-text-price .a-offscreen",
    ]:
        el = soup.select_one(sel)
        if el:
            p = _parse_price(el.get_text())
            if p > 0:
                result["price_original"] = p
                break

    # التقييم: من popover title أو alt attribute
    rating_el = soup.select_one("#acrPopover")
    if rating_el:
        title_attr = rating_el.get("title", "")
        m = re.search(r"([\d.]+)\s*(?:out of|من)", title_attr)
        if m:
            try:
                result["rating"] = float(m.group(1))
            except ValueError:
                pass

    if "rating" not in result:
        for el in soup.select(".a-icon-alt"):
            m = re.search(r"([\d.]+)\s*(?:out of|من)\s*5", el.get_text())
            if m:
                try:
                    result["rating"] = float(m.group(1))
                    break
                except ValueError:
                    pass

    # عدد التقييمات
    count_el = soup.select_one("#acrCustomerReviewText")
    if count_el:
        m = re.search(r"([\d,]+)", count_el.get_text())
        if m:
            try:
                result["rating_count"] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

    # الصورة
    img_el = soup.select_one("#landingImage")
    if img_el:
        src = img_el.get("data-old-hires") or img_el.get("src", "")
        if src and "http" in src:
            result["image_url"] = src

    return result


# ── Search result parser ──────────────────────────────────────────────────────────
def _parse_search_results(html: str, marketplace: str) -> list[dict]:
    results = []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    domain = _DOMAIN_MAP.get(marketplace, "www.amazon.sa")

    for div in soup.select("[data-asin]"):
        asin = div.get("data-asin", "").strip()
        if not asin or len(asin) != 10:
            continue

        # تخطي البطاقات الدعائية
        component_type = div.get("data-component-type", "")
        div_classes = " ".join(div.get("class", []))
        if "sp-sponsored-result" in component_type or "AdHolder" in div_classes:
            continue

        # العنوان
        title_el = div.select_one("h2 a span") or div.select_one("h2 span") or div.select_one(".a-text-normal")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title:
            continue

        # السعر: نأخذ أول .a-offscreen داخل .a-price غير مشطوب
        price_curr = 0.0
        price_orig = 0.0
        price_spans = div.select(".a-price:not(.a-text-price) .a-offscreen")
        if price_spans:
            price_curr = _parse_price(price_spans[0].get_text())
        orig_spans = div.select(".a-text-price .a-offscreen")
        if orig_spans:
            price_orig = _parse_price(orig_spans[0].get_text())

        # خصم
        discount_pct = 0
        if price_orig > 0 and price_curr > 0 and price_orig > price_curr:
            discount_pct = round((1 - price_curr / price_orig) * 100)
        else:
            badge = div.select_one(".a-badge-text")
            if badge:
                m = re.search(r"(\d+)%", badge.get_text())
                if m:
                    try:
                        discount_pct = int(m.group(1))
                    except ValueError:
                        pass

        # تقييم
        rating = 0.0
        rating_el = div.select_one(".a-icon-alt")
        if rating_el:
            m = re.search(r"([\d.]+)\s*(?:out of|من)\s*5", rating_el.get_text())
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass

        # عدد التقييمات
        rating_count = 0
        for el in div.select("span[aria-label]"):
            label = el.get("aria-label", "")
            m = re.search(r"^([\d,]+)$", label.replace(" ", ""))
            if m:
                try:
                    rating_count = int(m.group(1).replace(",", ""))
                    break
                except ValueError:
                    pass

        # صورة
        image_url = ""
        img_el = div.select_one("img.s-image") or div.select_one("img[data-asin]")
        if img_el:
            src = img_el.get("src", "")
            if src and "http" in src:
                image_url = src

        results.append({
            "asin": asin,
            "title": title,
            "price_current": price_curr,
            "price_original": price_orig or price_curr,
            "discount_pct": discount_pct,
            "rating": rating,
            "rating_count": rating_count,
            "image_url": image_url,
            "amazon_url": f"https://{domain}/dp/{asin}",
            "affiliate_url": _affiliate_url(asin, marketplace),
            "source": "scraper",
        })

    return results


# ── Bestsellers page parser ───────────────────────────────────────────────────────
def _parse_bestsellers_page(html: str, marketplace: str) -> list[str]:
    """يُرجع قائمة ASINs من صفحة bestsellers"""
    asins = []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # البطاقات الجديدة .zg-grid-general-faceout والقديمة .zg-item-immersion
    for el in soup.select(".zg-grid-general-faceout, .zg-item-immersion, [id^='zg-ordered-list'] li"):
        link = el.select_one("a[href*='/dp/']")
        if link:
            m = re.search(r"/dp/([A-Z0-9]{10})", link.get("href", ""))
            if m:
                asin = m.group(1)
                if asin not in asins:
                    asins.append(asin)
        if len(asins) >= 20:
            break

    return asins


# ── Main public API ───────────────────────────────────────────────────────────────

async def search_amazon(
    query: str,
    marketplace: str = "SA",
    num_results: int = 10,
) -> list[dict]:
    """
    يبحث في أمازون ويُرجع قائمة منتجات.
    يستخدم cache بـ TTL 3 ساعات.
    """
    from backend.config import get_setting
    ttl = get_setting("scraper_cache_ttl_hours", 6) / 2  # البحث: نصف TTL المنتج

    cache_key = f"search:{query.lower().strip()}:{marketplace}"
    cached = _cache_get(cache_key, ttl_hours=ttl)
    if cached is not None:
        logger.debug(f"Scraper cache hit: {cache_key}")
        return cached[:num_results]

    from urllib.parse import quote_plus
    domain = _DOMAIN_MAP.get(marketplace, "www.amazon.sa")
    url = f"https://{domain}/s?k={quote_plus(query)}"

    async with _semaphore:
        html, status = await _fetch(url)

    if not html:
        stale = _cache_get_stale(cache_key)
        if stale is not None:
            logger.info(f"Scraper: إرجاع stale cache لـ '{query}'")
            return stale[:num_results]
        return []

    results = _parse_search_results(html, marketplace)
    if results:
        _cache_set(cache_key, results)

    logger.info(f"Scraper search '{query}' ({marketplace}): {len(results)} نتيجة")
    return results[:num_results]


async def get_product_by_asin(
    asin: str,
    marketplace: str = "SA",
) -> Optional[dict]:
    """
    يجلب بيانات منتج محدد بـ ASIN.
    يستخدم cache بـ TTL 6 ساعات (من الإعدادات).
    """
    from backend.config import get_setting
    ttl = get_setting("scraper_cache_ttl_hours", 6)

    cache_key = f"asin:{asin}:{marketplace}"
    cached = _cache_get(cache_key, ttl_hours=ttl)
    if cached is not None:
        logger.debug(f"Scraper cache hit: {cache_key}")
        return cached

    domain = _DOMAIN_MAP.get(marketplace, "www.amazon.sa")
    url = f"https://{domain}/dp/{asin}"

    async with _semaphore:
        html, status = await _fetch(url)

    if not html:
        stale = _cache_get_stale(cache_key)
        if stale is not None:
            logger.info(f"Scraper: إرجاع stale cache لـ ASIN {asin}")
            return stale
        return None

    # استخراج البيانات: JSON-LD أولاً ثم CSS selectors
    ld = _parse_json_ld(html)
    css = _parse_product_html(html)

    # دمج: الأولوية لـ JSON-LD لأنه أدق
    merged = {**css, **{k: v for k, v in ld.items() if v}}

    title = merged.get("title", "").strip()
    if not title:
        logger.warning(f"Scraper: لم يُستخرج عنوان لـ ASIN {asin}")
        return None

    price_curr = merged.get("price_current", 0.0)
    price_orig = merged.get("price_original", 0.0) or price_curr
    discount_pct = 0
    if price_orig > 0 and price_curr > 0 and price_orig > price_curr:
        discount_pct = round((1 - price_curr / price_orig) * 100)

    result = {
        "asin": asin,
        "title": title,
        "price_current": price_curr,
        "price_original": price_orig,
        "discount_pct": discount_pct,
        "rating": merged.get("rating", 0.0),
        "rating_count": merged.get("rating_count", 0),
        "image_url": merged.get("image_url", ""),
        "amazon_url": f"https://{domain}/dp/{asin}",
        "affiliate_url": _affiliate_url(asin, marketplace),
        "source": "scraper",
    }

    _cache_set(cache_key, result)
    logger.info(f"Scraper ASIN {asin}: {title[:50]} — خصم {discount_pct}%")
    return result


async def get_bestsellers(
    category_slug: str,
    marketplace: str = "SA",
) -> list[dict]:
    """
    يجلب الأكثر مبيعاً لتصنيف معيّن.
    يستخدم cache بـ TTL 12 ساعة.
    """
    from backend.config import get_setting
    ttl = get_setting("scraper_cache_ttl_hours", 6) * 2  # bestsellers: ضعف TTL المنتج

    # تطبيق slug overrides
    slug = _SA_BESTSELLER_SLUG_MAP.get(category_slug, category_slug)

    cache_key = f"bestsellers:{slug}:{marketplace}"
    cached = _cache_get(cache_key, ttl_hours=ttl)
    if cached is not None:
        logger.debug(f"Scraper cache hit: {cache_key}")
        return cached

    domain = _DOMAIN_MAP.get(marketplace, "www.amazon.sa")
    url = f"https://{domain}/gp/bestsellers/{slug}"

    async with _semaphore:
        html, status = await _fetch(url)

    if not html:
        stale = _cache_get_stale(cache_key)
        return stale if stale is not None else []

    asins = _parse_bestsellers_page(html, marketplace)
    if not asins:
        logger.warning(f"Scraper bestsellers '{slug}': لم يُعثر على ASINs")
        return []

    # جلب بيانات كل ASIN بالتوازي (حد 4)
    sem4 = asyncio.Semaphore(4)

    async def fetch_one(asin: str) -> Optional[dict]:
        async with sem4:
            await asyncio.sleep(random.uniform(0.3, 0.8))
            return await get_product_by_asin(asin, marketplace)

    tasks = [fetch_one(a) for a in asins[:20]]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    results = [r for r in raw if isinstance(r, dict)]

    if results:
        _cache_set(cache_key, results)

    logger.info(f"Scraper bestsellers '{slug}' ({marketplace}): {len(results)} منتج")
    return results


# ── اختبار سريع من command line ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    async def _test_live():
        print("=== اختبار السكرابر ===")

        # بحث
        print("\n1. بحث عن 'coffee maker':")
        results = await search_amazon("coffee maker", "SA", 3)
        for r in results:
            print(f"  [{r['asin']}] {r['title'][:60]} — {r['price_current']} SAR ({r['discount_pct']}% off)")

        # منتج بـ ASIN
        test_asin = sys.argv[1] if len(sys.argv) > 1 else "B09SM24S8C"
        print(f"\n2. جلب ASIN {test_asin}:")
        product = await get_product_by_asin(test_asin, "SA")
        if product:
            print(f"  {product['title'][:60]}")
            print(f"  السعر: {product['price_current']} SAR (أصلي: {product['price_original']})")
            print(f"  التقييم: {product['rating']}/5 ({product['rating_count']} تقييم)")
        else:
            print("  لم يُعثر على المنتج")

        # bestsellers
        print("\n3. الأكثر مبيعاً — grocery:")
        bs = await get_bestsellers("grocery", "SA")
        for r in bs[:3]:
            print(f"  [{r['asin']}] {r['title'][:60]}")

    asyncio.run(_test_live())
