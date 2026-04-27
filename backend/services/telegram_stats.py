"""
telegram_stats.py — جلب إحصائيات قناة تلقرام (عدد المشتركين)
"""
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Cache 5 دقائق لتجنب hammering API تلقرام
_CACHE_TTL = 300
_cache: dict[str, tuple[dict, float]] = {}


async def get_subscriber_count() -> dict:
    """
    يجلب عدد مشتركي قناة تلقرام عبر getChatMemberCount.
    يُرجع: {"count": int, "ok": bool, "fetched_at": float, "error": Optional[str]}
    """
    from backend.config import get_api_key

    token = get_api_key("telegram_token")
    channel_id = get_api_key("telegram_channel_id")
    if not token or not channel_id:
        return {"count": 0, "ok": False, "error": "مفاتيح تلقرام غير مكتملة", "fetched_at": time.time()}

    cache_key = f"{token[:8]}:{channel_id}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    url = f"https://api.telegram.org/bot{token}/getChatMemberCount"
    payload = {"chat_id": channel_id}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("ok"):
                    result = {
                        "count": int(data.get("result", 0)),
                        "ok": True,
                        "error": None,
                        "fetched_at": now,
                    }
                    _cache[cache_key] = (result, now)
                    return result
                err = data.get("description", "خطأ غير معروف")
                # لا نُخزّن الأخطاء — اسمح بإعادة المحاولة في الطلب التالي
                return {"count": 0, "ok": False, "error": err, "fetched_at": now}
    except Exception as exc:
        return {"count": 0, "ok": False, "error": str(exc), "fetched_at": now}


async def get_chat_info() -> dict:
    """
    يجلب معلومات القناة (الاسم، الوصف، النوع) عبر getChat.
    يُرجع: {"title": str, "username": str, "description": str, "ok": bool, "error": Optional[str]}
    """
    from backend.config import get_api_key

    token = get_api_key("telegram_token")
    channel_id = get_api_key("telegram_channel_id")
    if not token or not channel_id:
        return {"ok": False, "error": "مفاتيح تلقرام غير مكتملة"}

    url = f"https://api.telegram.org/bot{token}/getChat"
    payload = {"chat_id": channel_id}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("ok"):
                    chat = data.get("result", {})
                    return {
                        "ok": True,
                        "error": None,
                        "title": chat.get("title", ""),
                        "username": chat.get("username", ""),
                        "description": chat.get("description", ""),
                        "type": chat.get("type", ""),
                    }
                return {"ok": False, "error": data.get("description", "خطأ غير معروف")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
