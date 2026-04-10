"""
trend_service.py — جلب الترندات من TikTok وInstagram عبر Apify
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── الدالة الرئيسية ──────────────────────────────────────────────────────────────
async def fetch_trends() -> list[dict]:
    """
    يجلب المنتجات الرائجة من TikTok وInstagram.
    يُرجع: [{title, title_ar, source, source_account, source_views, caption}, ...]
    """
    from backend.config import get_api_key, get_setting, SessionLocal
    from backend.models import WatchedAccount, TrendKeyword

    apify_token = get_api_key("apify")
    if not apify_token:
        logger.warning("مفتاح Apify غير موجود — يُرجع بيانات تجريبية")
        return _mock_trends()

    enable_tiktok = get_setting("enable_tiktok", True)
    enable_instagram = get_setting("enable_instagram", True)

    # اجلب الحسابات المراقبة
    with SessionLocal() as db:
        accounts = db.query(WatchedAccount).filter(WatchedAccount.active == True).all()
        keywords = db.query(TrendKeyword).filter(TrendKeyword.active == True).all()

    tiktok_accounts = [a.username for a in accounts if a.platform == "tiktok" and enable_tiktok]
    instagram_accounts = [a.username for a in accounts if a.platform == "instagram" and enable_instagram]
    keyword_list = [k.keyword for k in keywords]

    tasks = []
    if tiktok_accounts:
        tasks.append(_fetch_tiktok(apify_token, tiktok_accounts))
    if instagram_accounts:
        tasks.append(_fetch_instagram(apify_token, instagram_accounts))
    if keyword_list:
        tasks.append(_fetch_by_keywords(apify_token, keyword_list))

    if not tasks:
        logger.info("لا توجد حسابات أو كلمات مفتاحية مراقبة — يُرجع قائمة فارغة")
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_products = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"خطأ في جلب الترندات: {r}")
        elif isinstance(r, list):
            all_products.extend(r)

    # إزالة التكرار
    seen = set()
    unique = []
    for p in all_products:
        key = p.get("title", "")[:50]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    logger.info(f"تم جلب {len(unique)} منتج مرشح من الترندات")
    _update_account_stats(accounts, len(unique))
    return unique


async def _fetch_tiktok(token: str, usernames: list[str]) -> list[dict]:
    """جلب منشورات TikTok عبر Apify"""
    try:
        from apify_client import ApifyClient
        client = ApifyClient(token)

        run = await asyncio.to_thread(
            client.actor("clockworks/tiktok-scraper").call,
            run_input={"profiles": usernames, "maxVideos": 10},
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        products = []
        for item in items:
            product = _parse_tiktok_item(item)
            if product:
                products.append(product)
        return products

    except ImportError:
        logger.warning("apify-client غير مثبتة")
        return []
    except Exception as exc:
        logger.error(f"خطأ في Apify TikTok: {exc}")
        return []


async def _fetch_instagram(token: str, usernames: list[str]) -> list[dict]:
    """جلب منشورات Instagram عبر Apify"""
    try:
        from apify_client import ApifyClient
        client = ApifyClient(token)

        run = await asyncio.to_thread(
            client.actor("apify/instagram-post-scraper").call,
            run_input={"usernames": usernames, "resultsLimit": 10},
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        products = []
        for item in items:
            product = _parse_instagram_item(item)
            if product:
                products.append(product)
        return products

    except ImportError:
        logger.warning("apify-client غير مثبتة")
        return []
    except Exception as exc:
        logger.error(f"خطأ في Apify Instagram: {exc}")
        return []


async def _fetch_by_keywords(token: str, keywords: list[str]) -> list[dict]:
    """بحث مباشر بالكلمات المفتاحية"""
    results = []
    for keyword in keywords[:5]:  # حد أقصى 5 كلمات للاختبار
        # استخدم الكلمة المفتاحية كـ query مباشر لأمازون
        results.append({
            "title": keyword,
            "title_ar": keyword,
            "source": "keyword",
            "source_account": "keyword_search",
            "source_views": 0,
            "caption": keyword,
        })
    return results


def _parse_tiktok_item(item: dict) -> Optional[dict]:
    """تحليل بيانات TikTok"""
    try:
        views = item.get("playCount", 0) or item.get("stats", {}).get("playCount", 0)
        caption = item.get("text", "") or item.get("desc", "")
        author = item.get("authorMeta", {}).get("name", "") or item.get("author", {}).get("uniqueId", "")

        if not caption:
            return None

        return {
            "title": caption[:100],
            "title_ar": "",
            "source": "tiktok",
            "source_account": f"@{author}",
            "source_views": int(views) if views else 0,
            "caption": caption,
        }
    except Exception:
        return None


def _parse_instagram_item(item: dict) -> Optional[dict]:
    """تحليل بيانات Instagram"""
    try:
        likes = item.get("likesCount", 0) or 0
        caption = item.get("caption", "") or ""
        owner = item.get("ownerUsername", "") or item.get("owner", {}).get("username", "")

        if not caption:
            return None

        return {
            "title": caption[:100],
            "title_ar": "",
            "source": "instagram",
            "source_account": f"@{owner}",
            "source_views": int(likes) if likes else 0,
            "caption": caption,
        }
    except Exception:
        return None


async def extract_product_name_with_ai(caption: str) -> str:
    """استخدام Claude لاستخراج اسم المنتج من الكابشن"""
    from backend.config import get_api_key
    api_key = get_api_key("anthropic")
    if not api_key:
        return caption[:80]

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": f"من هذا النص استخرج اسم المنتج التجاري فقط بالعربي والإنجليزي (جملة واحدة قصيرة): {caption[:300]}"
            }],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.warning(f"فشل استخراج اسم المنتج بالذكاء الاصطناعي: {exc}")
        return caption[:80]


def _update_account_stats(accounts, total_found: int):
    """تحديث إحصاءات الحسابات"""
    from backend.config import SessionLocal
    from datetime import datetime
    try:
        with SessionLocal() as db:
            for acc in accounts:
                db_acc = db.get(type(acc), acc.id)
                if db_acc:
                    db_acc.last_checked = datetime.utcnow()
                    db_acc.posts_found = (db_acc.posts_found or 0) + (total_found // max(len(accounts), 1))
            db.commit()
    except Exception as exc:
        logger.error(f"خطأ في تحديث إحصاءات الحسابات: {exc}")


def _mock_trends() -> list[dict]:
    """بيانات تجريبية عند غياب Apify"""
    return [
        {
            "title": "Sony WH-1000XM5 Wireless Headphones",
            "title_ar": "سماعات سوني اللاسلكية",
            "source": "tiktok",
            "source_account": "@techreviews_sa",
            "source_views": 2500000,
            "caption": "أفضل سماعات رأس لاسلكية في 2024 من سوني",
        },
        {
            "title": "Samsung Galaxy S24 Ultra Case",
            "title_ar": "كفر سامسونج جالاكسي S24 الترا",
            "source": "instagram",
            "source_account": "@gadgets_gulf",
            "source_views": 450000,
            "caption": "أجمل كفرات حماية لجوال سامسونج الجديد",
        },
        {
            "title": "Air Fryer 5L",
            "title_ar": "قلاية هوائية 5 لتر",
            "source": "tiktok",
            "source_account": "@cooking_arabic",
            "source_views": 1800000,
            "caption": "القلاية الهوائية غيّرت حياتي",
        },
    ]
