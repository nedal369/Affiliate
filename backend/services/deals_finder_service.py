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
    # ── المنزل والمطبخ ─────────────────────────────
    {"id": "groceries", "name_ar": "المقاضي والبقالة", "icon": "🛒",
     "keywords": ["coffee beans", "olive oil", "honey", "dates", "nuts mix", "spices set"]},
    {"id": "kitchen_appliances", "name_ar": "الأجهزة المنزلية الصغيرة", "icon": "🍳",
     "keywords": ["air fryer", "rice cooker", "blender", "coffee maker", "stand mixer", "electric kettle"]},
    {"id": "kitchen_tools", "name_ar": "أدوات المطبخ", "icon": "🍴",
     "keywords": ["non stick pan set", "kitchen knife set", "food storage containers", "cookware set", "cutting board", "measuring cups"]},
    {"id": "cleaning", "name_ar": "التنظيف والعناية بالمنزل", "icon": "🧹",
     "keywords": ["vacuum cleaner", "robot vacuum", "steam mop", "air purifier", "humidifier", "iron steamer"]},
    {"id": "bedding", "name_ar": "أغطية وأقمشة المنزل", "icon": "🛏️",
     "keywords": ["bed sheets king", "memory foam pillow", "comforter set", "blackout curtains", "bath towel set", "weighted blanket"]},
    {"id": "home_decor", "name_ar": "ديكور المنزل", "icon": "🪴",
     "keywords": ["wall clock", "led lights strip", "table lamp", "wall art", "artificial plants", "diffuser oil"]},

    # ── العناية الشخصية ─────────────────────────────
    {"id": "perfumes", "name_ar": "العطور", "icon": "🌸",
     "keywords": ["men perfume", "women perfume", "oud perfume", "body spray", "cologne", "incense bakhoor"]},
    {"id": "skincare", "name_ar": "العناية بالبشرة", "icon": "✨",
     "keywords": ["face cream", "vitamin c serum", "sunscreen", "face wash", "moisturizer", "anti aging cream"]},
    {"id": "haircare", "name_ar": "العناية بالشعر", "icon": "💇",
     "keywords": ["hair dryer", "hair straightener", "hair oil", "shampoo set", "hair clipper", "scalp brush"]},
    {"id": "personal_care", "name_ar": "العناية الشخصية", "icon": "🪥",
     "keywords": ["electric toothbrush", "shaver men", "epilator", "nail kit", "facial cleansing brush", "teeth whitening"]},

    # ── الإلكترونيات والتقنية ─────────────────────────
    {"id": "audio", "name_ar": "الصوتيات والسماعات", "icon": "🎧",
     "keywords": ["wireless earbuds", "noise cancelling headphones", "bluetooth speaker", "soundbar", "studio headphones", "portable speaker"]},
    {"id": "phone_accessories", "name_ar": "اكسسوارات الجوال", "icon": "📱",
     "keywords": ["phone charger fast", "power bank 20000", "phone case", "screen protector", "wireless charger", "car phone holder"]},
    {"id": "smart_home", "name_ar": "المنزل الذكي", "icon": "💡",
     "keywords": ["smart bulb", "smart plug", "security camera", "video doorbell", "smart speaker", "wifi extender"]},
    {"id": "wearables", "name_ar": "الساعات الذكية واللياقة", "icon": "⌚",
     "keywords": ["smart watch", "fitness tracker", "apple watch band", "heart rate monitor", "smart ring", "kids smart watch"]},
    {"id": "gaming", "name_ar": "الألعاب والقيمنق", "icon": "🎮",
     "keywords": ["gaming headset", "mechanical keyboard rgb", "gaming mouse", "gaming chair", "console controller", "gaming monitor"]},

    # ── الأسرة والأطفال ─────────────────────────────
    {"id": "kids_toys", "name_ar": "ألعاب الأطفال", "icon": "🧸",
     "keywords": ["lego building blocks", "educational toys", "remote control car", "kids puzzle", "doll house", "stuffed animals"]},
    {"id": "baby", "name_ar": "مستلزمات الأطفال الرضع", "icon": "👶",
     "keywords": ["baby monitor", "baby stroller", "diaper bag", "baby bottle warmer", "baby carrier", "kids tablet"]},

    # ── الرياضة والصحة ─────────────────────────────
    {"id": "sports", "name_ar": "الرياضة واللياقة", "icon": "🏋️",
     "keywords": ["adjustable dumbbells", "yoga mat", "resistance bands set", "treadmill foldable", "exercise bike", "protein shaker"]},
    {"id": "outdoor", "name_ar": "الرحلات والتخييم", "icon": "⛺",
     "keywords": ["camping tent", "portable cooler", "sleeping bag", "camping chair", "outdoor grill", "hiking backpack"]},

    # ── أخرى مطلوبة سعودياً ─────────────────────────
    {"id": "car", "name_ar": "اكسسوارات السيارة", "icon": "🚗",
     "keywords": ["car vacuum", "dash cam", "car air freshener", "car seat cover", "tire inflator", "jump starter"]},
    {"id": "office", "name_ar": "المكتب والعمل من المنزل", "icon": "💻",
     "keywords": ["ergonomic office chair", "monitor stand", "desk lamp led", "laptop stand", "webcam hd", "wireless mouse"]},
    {"id": "islamic", "name_ar": "المنتجات الإسلامية", "icon": "🕌",
     "keywords": ["prayer mat", "quran reader pen", "digital tasbih", "azan clock", "prayer rug travel", "islamic decor"]},
]

_RAPIDAPI_HOST = "real-time-amazon-data.p.rapidapi.com"
_BASE_URL = f"https://{_RAPIDAPI_HOST}"


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


async def _rapidapi_search(keyword: str, country: str, api_key: str) -> list[dict]:
    """بحث في Amazon عبر RapidAPI /search endpoint"""
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


# خريطة من id التصنيف الداخلي إلى category slug عند RapidAPI bestsellers
_BESTSELLER_CATEGORY_MAP = {
    "groceries": "grocery",
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
    "smart_home": "smart-home",
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


async def _rapidapi_bestsellers(category_id: str, country: str, api_key: str) -> list[dict]:
    """جلب الأكثر مبيعاً للتصنيف كـ fallback"""
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": _RAPIDAPI_HOST,
    }
    category_slug = _BESTSELLER_CATEGORY_MAP.get(category_id, "aps")
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


_STOP_WORDS = {
    "the", "a", "an", "for", "with", "and", "of", "to", "in", "on", "by",
    "new", "best", "premium", "professional", "set", "kit", "pack", "size",
}


def _product_signature(title: str) -> str:
    """تستخرج 'نوع' المنتج من العنوان لتجنب التكرار النوعي.
    مثال: 'Anker Wireless Earbuds Pro' و 'Sony Wireless Earbuds Plus' → نفس النوع 'wireless earbuds'
    """
    if not title:
        return ""
    words = [w.lower().strip(".,!?-()[]{}") for w in title.split()]
    meaningful = [w for w in words if w and w not in _STOP_WORDS and len(w) > 2 and not w.isdigit()]
    return " ".join(meaningful[:2]) if len(meaningful) >= 2 else (meaningful[0] if meaningful else "")


def _sort_and_deduplicate(products: list[dict]) -> list[dict]:
    """إزالة التكرار وترتيب حسب الجودة + تنويع المنتجات"""
    seen_asins = set()
    unique = []
    for p in products:
        if p["asin"] not in seen_asins:
            seen_asins.add(p["asin"])
            unique.append(p)

    def score(p):
        discount = p.get("discount_pct", 0)
        rating = p.get("rating", 0)
        count = min(p.get("rating_count", 0), 10000)
        return discount * 0.5 + rating * 10 + count / 1000

    unique.sort(key=score, reverse=True)

    # تنويع: حد أقصى منتجين بنفس "نوع" المنتج (نفس بداية العنوان)
    diverse = []
    type_counts: dict[str, int] = {}
    overflow = []  # المنتجات التي تجاوزت الحد، نرجع لها إذا لم نملأ 15
    for p in unique:
        sig = _product_signature(p.get("title", ""))
        if type_counts.get(sig, 0) < 2:
            diverse.append(p)
            type_counts[sig] = type_counts.get(sig, 0) + 1
        else:
            overflow.append(p)
        if len(diverse) >= 15:
            break

    # إذا حصلنا على أقل من 15، نملأ من overflow
    if len(diverse) < 15:
        diverse.extend(overflow[:15 - len(diverse)])

    return diverse


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

        # تنظيف: إزالة code blocks (```json ... ```)
        import re
        clean = re.sub(r"```(?:json)?\s*", "", raw or "").replace("```", "").strip()

        # البحث عن أول كائن JSON يحتوي "selected"
        match = re.search(r'\{[^{}]*"selected"[^{}]*\}', clean, re.DOTALL)
        json_str = match.group(0) if match else None

        # fallback: إذا لم نجد بالـ regex، جرّب من أول { لآخر }
        if not json_str:
            start = clean.find("{")
            end = clean.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = clean[start:end]

        if json_str:
            try:
                parsed = json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.warning(f"AI returned invalid JSON for '{category_name}': {e} — raw: {raw[:200]}")
                parsed = None

            if parsed:
                indices = parsed.get("selected", [])
                selected = []
                for idx in indices:
                    try:
                        i = int(idx)
                        if 1 <= i <= len(products):
                            selected.append(products[i - 1])
                    except (ValueError, TypeError):
                        continue
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

    api_key = get_api_key("rapidapi_key")
    if not api_key:
        raise ValueError("مفتاح RapidAPI غير موجود — أضفه من صفحة مفاتيح API")

    marketplace = get_setting("amazon_marketplace", "SA")

    results = {}

    async def process_category(cat_id: str):
        category = _get_category(cat_id)
        if not category:
            logger.warning(f"تصنيف غير موجود: {cat_id}")
            return

        logger.info(f"جاري البحث في تصنيف: {category['name_ar']}")
        all_products = []

        # نبحث بكل keyword مع تشغيل متوازي
        tasks = [
            _rapidapi_search(kw, marketplace, api_key)
            for kw in category["keywords"]
        ]
        search_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in search_results:
            if isinstance(result, list):
                all_products.extend(result)

        # fallback للأكثر مبيعاً إذا كانت النتائج قليلة
        if len(all_products) < 5:
            logger.info(f"نتائج قليلة لـ '{category['name_ar']}' — جرّب bestsellers كـ fallback")
            bestsellers = await _rapidapi_bestsellers(cat_id, marketplace, api_key)
            all_products.extend(bestsellers)

        if not all_products:
            logger.warning(f"لا توجد منتجات لتصنيف '{category['name_ar']}'")
            results[cat_id] = []
            return

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
