"""
deals_finder_service.py — اكتشاف أفضل العروض بالذكاء الاصطناعي
يعمل بدون Amazon PA API — يستخدم RapidAPI + AI لاختيار أفضل العروض
"""
import json
import logging
import asyncio
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── التصنيفات المدعومة ────────────────────────────────────────────────────────────

CATEGORIES = [
    {
        "id": "household",
        "name_ar": "مستلزمات المنزل",
        "icon": "🏠",
        "keywords": ["kitchen appliances", "vacuum cleaner", "air fryer", "coffee maker"],
    },
    {
        "id": "gaming",
        "name_ar": "أجهزة الألعاب",
        "icon": "🎮",
        "keywords": ["gaming headset", "gaming controller", "gaming keyboard", "gaming mouse"],
    },
    {
        "id": "electronics",
        "name_ar": "إلكترونيات",
        "icon": "📱",
        "keywords": ["wireless earbuds", "bluetooth speaker", "smart watch", "power bank"],
    },
    {
        "id": "beauty",
        "name_ar": "العناية والتجميل",
        "icon": "💄",
        "keywords": ["electric toothbrush", "hair dryer", "face wash", "perfume"],
    },
    {
        "id": "sports",
        "name_ar": "رياضة ولياقة",
        "icon": "🏋️",
        "keywords": ["yoga mat", "resistance bands", "water bottle", "fitness tracker"],
    },
    {
        "id": "kids",
        "name_ar": "منتجات الأطفال",
        "icon": "🧸",
        "keywords": ["educational toys", "lego", "baby monitor", "kids headphones"],
    },
    {
        "id": "fashion",
        "name_ar": "ملابس وأزياء",
        "icon": "👗",
        "keywords": ["sunglasses", "backpack", "wallet", "belt"],
    },
    {
        "id": "office",
        "name_ar": "مكتب وقرطاسية",
        "icon": "📝",
        "keywords": ["mechanical keyboard", "webcam", "desk lamp", "mouse pad"],
    },
]

_RAPIDAPI_HOST = "real-time-amazon-data.p.rapidapi.com"
_BASE_URL = f"https://{_RAPIDAPI_HOST}"

# ── خريطة التصنيف الداخلي → bestseller slug للسكرابر ────────────────────────────
_BESTSELLER_SLUG_MAP = {
    "groceries": "food-and-grocery",
    "kitchen_appliances": "kitchen",
    "kitchen_tools": "kitchen",
    "cleaning": "kitchen",
    "bedding": "home",
    "home_decor": "home",
    "perfumes": "beauty",
    "skincare": "beauty",
    "haircare": "beauty",
    "personal_care": "beauty",
    "audio": "electronics",
    "phone_accessories": "wireless",
    "smart_home": "amazon-devices",
    "wearables": "electronics",
    "gaming": "videogames",
    "kids_toys": "toys",
    "baby": "baby-products",
    "sports": "sports",
    "outdoor": "sports",
    "car": "automotive",
    "office": "office-products",
    "islamic": "home",
}


def get_all_categories() -> list[dict]:
    return [{"id": c["id"], "name_ar": c["name_ar"], "icon": c["icon"]} for c in CATEGORIES]


def _get_category(category_id: str) -> Optional[dict]:
    return next((c for c in CATEGORIES if c["id"] == category_id), None)


def _build_affiliate_url(asin: str, marketplace: str = "SA") -> str:
    from backend.config import get_api_key, get_setting
    tag = get_api_key("amazon_associate_tag") or get_setting("amazon_associate_tag", "nedal01-21")
    domain_map = {"SA": "amazon.sa", "AE": "amazon.ae", "EG": "amazon.eg", "US": "amazon.com", "GB": "amazon.co.uk"}
    domain = domain_map.get(marketplace, "amazon.sa")
    return f"https://www.{domain}/dp/{asin}?tag={tag}"


async def _scraper_search(keyword: str, marketplace: str) -> list[dict]:
    """بحث في Amazon عبر السكرابر المجاني"""
    from backend.services.amazon_scraper import search_amazon
    results = await search_amazon(keyword, marketplace, num_results=10)
    # تأكد من وجود affiliate_url
    for r in results:
        if not r.get("affiliate_url"):
            r["affiliate_url"] = _build_affiliate_url(r["asin"], marketplace)
    return results


async def _scraper_bestsellers(category_id: str, marketplace: str) -> list[dict]:
    """جلب الأكثر مبيعاً عبر السكرابر كـ fallback"""
    from backend.services.amazon_scraper import get_bestsellers
    slug = _BESTSELLER_SLUG_MAP.get(category_id, "aps")
    results = await get_bestsellers(slug, marketplace)
    for r in results:
        if not r.get("affiliate_url"):
            r["affiliate_url"] = _build_affiliate_url(r["asin"], marketplace)
    return results


async def _rapidapi_search(keyword: str, country: str, api_key: str) -> list[dict]:
    """بحث في Amazon عبر RapidAPI /search endpoint (fallback قديم)"""
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": _RAPIDAPI_HOST,
    }
    params = {
        "query": keyword,
        "country": country,
        "sort_by": "RELEVANCE",
        "page": "1",
        "num_results": "10",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_BASE_URL}/search",
                headers=headers,
                params=params,
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"RapidAPI /search {resp.status} for '{keyword}': {text[:150]}")
                    return []
                data = await resp.json()
    except Exception as exc:
        logger.error(f"RapidAPI /search error for '{keyword}': {exc}")
        return []

    raw_products = (
        data.get("data", {}).get("products")
        or data.get("products")
        or []
    )

    results = []
    for item in raw_products:
        parsed = _parse_product(item, country)
        if parsed:
            results.append(parsed)
    return results


async def _rapidapi_bestsellers(category_id: str, country: str, api_key: str) -> list[dict]:
    """جلب الأكثر مبيعاً للتصنيف عبر RapidAPI (fallback قديم)"""
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": _RAPIDAPI_HOST,
    }
    category_slug = _BESTSELLER_SLUG_MAP.get(category_id, "aps")
    params = {"country": country, "category": category_slug, "page": "1"}
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_BASE_URL}/best-sellers",
                headers=headers,
                params=params,
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
    except Exception as exc:
        logger.error(f"RapidAPI /best-sellers error: {exc}")
        return []

    raw = (
        data.get("data", {}).get("bestsellers")
        or data.get("data", {}).get("products")
        or data.get("bestsellers")
        or []
    )
    results = []
    for item in raw:
        parsed = _parse_product(item, country)
        if parsed:
            results.append(parsed)
    return results


def _parse_product(item: dict, country: str) -> Optional[dict]:
    """تحويل نتيجة RapidAPI إلى dict موحّد"""
    import re

    def parse_price(val) -> float:
        if not val:
            return 0.0
        cleaned = re.sub(r"[^\d.]", "", str(val).replace(",", "."))
        parts = cleaned.split(".")
        if len(parts) > 2:
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    asin = item.get("asin") or item.get("product_asin", "")
    if not asin:
        return None

    title = (item.get("product_title") or item.get("title", "")).strip()
    if not title:
        return None

    price_curr = parse_price(item.get("product_price") or item.get("price", ""))
    price_orig = parse_price(item.get("product_original_price") or item.get("original_price", "")) or price_curr

    discount_pct = 0
    if price_orig > 0 and price_curr > 0 and price_orig > price_curr:
        discount_pct = round((1 - price_curr / price_orig) * 100)
    else:
        raw_disc = item.get("product_discount") or item.get("discount", "")
        if raw_disc:
            try:
                discount_pct = int(re.sub(r"[^\d]", "", str(raw_disc)) or 0)
            except ValueError:
                pass

    rating = 0.0
    raw_r = item.get("product_star_rating") or item.get("stars") or item.get("rating", "")
    if raw_r:
        try:
            rating = float(re.sub(r"[^\d.]", "", str(raw_r)) or 0)
        except ValueError:
            pass

    rating_count = 0
    raw_c = item.get("product_num_ratings") or item.get("ratings_total") or item.get("num_ratings", 0)
    try:
        rating_count = int(str(raw_c).replace(",", "").replace(".", "")) if raw_c else 0
    except ValueError:
        pass

    image_url = (
        item.get("product_photo")
        or item.get("product_main_image_url")
        or item.get("thumbnail")
        or item.get("image", "")
    )

    domain_map = {"SA": "amazon.sa", "AE": "amazon.ae", "EG": "amazon.eg", "US": "amazon.com", "GB": "amazon.co.uk"}
    domain = domain_map.get(country, "amazon.sa")
    amazon_url = item.get("product_url") or f"https://www.{domain}/dp/{asin}"

    return {
        "asin": asin,
        "title": title,
        "amazon_url": amazon_url,
        "affiliate_url": _build_affiliate_url(asin, country),
        "price_original": price_orig,
        "price_current": price_curr,
        "discount_pct": discount_pct,
        "rating": rating,
        "rating_count": rating_count,
        "image_url": image_url,
    }


def _sort_and_deduplicate(products: list[dict]) -> list[dict]:
    """إزالة التكرار وترتيب حسب الجودة"""
    seen = set()
    unique = []
    for p in products:
        if p["asin"] not in seen:
            seen.add(p["asin"])
            unique.append(p)

    def score(p):
        discount = p.get("discount_pct", 0)
        rating = p.get("rating", 0)
        count = min(p.get("rating_count", 0), 10000)
        return discount * 0.5 + rating * 10 + count / 1000

    unique.sort(key=score, reverse=True)
    return unique


async def _ai_select_top5(products: list[dict], category_name: str) -> list[dict]:
    """
    يطلب من AI اختيار أفضل 5 منتجات من القائمة.
    إذا فشل AI يُرجع أفضل 5 حسب الترتيب الحسابي.
    """
    if len(products) <= 5:
        return products

    try:
        from backend.services.ai_service import call_ai

        products_summary = []
        for i, p in enumerate(products[:20]):
            products_summary.append(
                f"{i+1}. ASIN:{p['asin']} | {p['title'][:60]} | "
                f"السعر:{p['price_current']} | الخصم:{p['discount_pct']}% | "
                f"التقييم:{p['rating']}/5 ({p['rating_count']} تقييم)"
            )

        prompt = f"""فيما يلي قائمة منتجات في تصنيف "{category_name}":

{chr(10).join(products_summary)}

اختر أفضل 5 منتجات فقط بناءً على:
1. نسبة الخصم (كلما زادت كان أفضل)
2. التقييم (كلما ارتفع كان أفضل)
3. شعبية المنتج وعدد التقييمات
4. قيمة المنتج (هل السعر مناسب بعد الخصم؟)

أجب بـ JSON فقط — مصفوفة من أرقام المنتجات المختارة (1-based) بالترتيب من الأفضل للأقل:
{{"selected": [رقم1, رقم2, رقم3, رقم4, رقم5]}}

لا تضف أي نص آخر خارج الـ JSON."""

        system = "أنت خبير في اكتشاف أفضل العروض على أمازون للسوق السعودي."
        raw = await call_ai(prompt, system=system)

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            indices = parsed.get("selected", [])
            selected = []
            for idx in indices:
                if 1 <= idx <= len(products):
                    selected.append(products[idx - 1])
            if len(selected) >= 3:
                logger.info(f"AI اختار {len(selected)} منتجات لـ '{category_name}'")
                return selected[:5]
    except Exception as exc:
        logger.warning(f"AI selection failed for '{category_name}': {exc} — using score-based fallback")

    return products[:5]


async def discover_deals(category_ids: list[str]) -> dict:
    """
    الوظيفة الرئيسية: تجلب أفضل العروض للتصنيفات المحددة.
    تُرجع: {"category_id": [list of 5 deals], ...}
    """
    from backend.config import get_api_key, get_setting

    marketplace = get_setting("amazon_marketplace", "SA")
    data_source = get_setting("amazon_data_source", "scraper")
    api_key = get_api_key("rapidapi_key") if data_source != "scraper" else None
    seen_asins = seen_asins or {}

    results = {}

    async def _search_kw(kw: str) -> list[dict]:
        if data_source == "rapidapi" and api_key:
            return await _rapidapi_search(kw, marketplace, api_key)
        if data_source == "auto" and api_key:
            r = await _scraper_search(kw, marketplace)
            if not r:
                r = await _rapidapi_search(kw, marketplace, api_key)
            return r
        return await _scraper_search(kw, marketplace)

    async def _fallback_bestsellers(cat_id: str) -> list[dict]:
        if data_source == "rapidapi" and api_key:
            return await _rapidapi_bestsellers(cat_id, marketplace, api_key)
        if data_source == "auto" and api_key:
            r = await _scraper_bestsellers(cat_id, marketplace)
            if not r:
                r = await _rapidapi_bestsellers(cat_id, marketplace, api_key)
            return r
        return await _scraper_bestsellers(cat_id, marketplace)

    async def process_category(cat_id: str):
        category = _get_category(cat_id)
        if not category:
            logger.warning(f"تصنيف غير موجود: {cat_id}")
            return

        logger.info(f"جاري البحث في تصنيف: {category['name_ar']}")
        all_products = []

        tasks = [_search_kw(kw) for kw in category["keywords"]]
        search_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in search_results:
            if isinstance(result, list):
                all_products.extend(result)

        # fallback للأكثر مبيعاً إذا كانت النتائج قليلة
        if len(all_products) < 5:
            logger.info(f"نتائج قليلة لـ '{category['name_ar']}' — جرّب bestsellers كـ fallback")
            bestsellers = await _fallback_bestsellers(cat_id)
            all_products.extend(bestsellers)

        if not all_products:
            logger.warning(f"لا توجد منتجات لتصنيف '{category['name_ar']}'")
            results[cat_id] = []
            return

        # استبعاد المنتجات التي سبق عرضها
        excluded = set(seen_asins.get(cat_id, []))
        if excluded:
            before = len(all_products)
            all_products = [p for p in all_products if p["asin"] not in excluded]
            logger.info(f"استبعدنا {before - len(all_products)} منتج سبق عرضه في '{category['name_ar']}'")

        if not all_products:
            logger.info(f"استُنفذت المنتجات الجديدة في '{category['name_ar']}' — نعيد كل المنتجات")
            tasks2 = [_search_kw(kw) for kw in category["keywords"]]
            r2 = await asyncio.gather(*tasks2, return_exceptions=True)
            all_products = [p for res in r2 if isinstance(res, list) for p in res]


        # ترتيب وإزالة التكرار
        unique_sorted = _sort_and_deduplicate(all_products)

        # AI يختار أفضل 5
        top5 = await _ai_select_top5(unique_sorted, category["name_ar"])

        # إضافة معلومات التصنيف
        for p in top5:
            p["category_id"] = cat_id
            p["category_name"] = category["name_ar"]
            p["category_icon"] = category["icon"]

        results[cat_id] = top5
        logger.info(f"تصنيف '{category['name_ar']}': {len(top5)} منتج")

    # معالجة متوازية لكل التصنيفات (بحد أقصى 3 متزامنة)
    semaphore = asyncio.Semaphore(3)

    async def process_with_semaphore(cat_id):
        async with semaphore:
            await process_category(cat_id)

    await asyncio.gather(*[process_with_semaphore(cid) for cid in category_ids])

    return results


async def auto_discover_and_queue(
    max_categories: int = 5,
    products_per_category: int = 3,
) -> dict:
    """
    اكتشاف تلقائي: يختار أشهر التصنيفات، يجلب العروض، يولّد المحتوى، يضيفها للانتظار.
    يُرجع: {"added": N, "skipped": N, "categories": [...]}
    """
    from backend.services.ai_service import generate_content
    from backend.services.amazon_service import is_product_posted_before
    from backend.config import SessionLocal, get_setting
    from backend.models import Product

    # اختار أول max_categories من القائمة (الأشهر في السوق السعودي)
    selected_cats = [c["id"] for c in CATEGORIES[:max_categories]]

    logger.info(f"اكتشاف تلقائي: {selected_cats}")

    # اكتشف العروض
    deals_by_cat = await discover_deals(selected_cats)

    primary_llm = get_setting("primary_llm", "auto") or "auto"
    added = 0
    skipped = 0
    processed_categories = []

    for cat_id, deals in deals_by_cat.items():
        if not deals:
            continue

        # خذ أفضل products_per_category منتج
        top_deals = deals[:products_per_category]
        cat_added = 0

        for deal in top_deals:
            asin = deal.get("asin", "")
            try:
                if asin and await is_product_posted_before(asin):
                    skipped += 1
                    continue

                # ولّد المحتوى
                content, model_used = await generate_content(deal, llm=primary_llm)
                title = deal.get("title", "")

                with SessionLocal() as db:
                    product = Product(
                        title=title,
                        title_ar=title,
                        source="deals_finder",
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
                        status="pending",
                    )
                    db.add(product)
                    db.commit()

                added += 1
                cat_added += 1
            except Exception as exc:
                logger.error(f"خطأ في إضافة {asin} للانتظار: {exc}")

        if cat_added > 0:
            processed_categories.append({
                "id": cat_id,
                "name": next((c["name_ar"] for c in CATEGORIES if c["id"] == cat_id), cat_id),
                "added": cat_added,
            })

    logger.info(f"اكتشاف تلقائي اكتمل: {added} أُضيف، {skipped} مكرر")
    return {"added": added, "skipped": skipped, "categories": processed_categories}
