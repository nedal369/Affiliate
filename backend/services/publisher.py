"""
publisher.py — نشر المحتوى على Telegram + WhatsApp + X
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ── النشر الرئيسي ────────────────────────────────────────────────────────────────
async def publish_product(product_id: int) -> dict:
    """
    ينشر المنتج على كل القنوات المفعّلة.
    يُرجع: {"telegram": bool, "whatsapp": bool, "x": bool, "errors": {...}}
    """
    from backend.config import get_setting, SessionLocal
    from backend.models import Product, PublishLog

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            return {"success": False, "error": "المنتج غير موجود"}

        content = product.ai_content or ""
        image_url = product.image_url or ""
        product_data = {
            "id": product.id,
            "title": product.title,
            "content": content,
            "image_url": image_url,
        }

    enable_telegram = get_setting("enable_telegram", True)
    enable_whatsapp = get_setting("enable_whatsapp", False)
    enable_x = get_setting("enable_x", False)

    tasks = {}
    if enable_telegram:
        tasks["telegram"] = publish_to_telegram(content, image_url)
    if enable_whatsapp:
        tasks["whatsapp"] = publish_to_whatsapp(content, image_url)
    if enable_x:
        tasks["x"] = publish_to_x(content, image_url)

    results = {}
    errors = {}

    if tasks:
        channel_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for channel, result in zip(tasks.keys(), channel_results):
            if isinstance(result, Exception):
                results[channel] = False
                errors[channel] = str(result)
            else:
                success, error_msg = result if isinstance(result, tuple) else (result, None)
                results[channel] = success
                if error_msg:
                    errors[channel] = error_msg

    # سجّل النتائج في قاعدة البيانات
    _log_results(product_id, results, errors)

    # حدّث حالة المنتج
    all_success = any(results.values()) if results else False
    _update_product_status(product_id, "published" if all_success else "failed")

    return {**results, "errors": errors, "success": all_success}


# ── تيليغرام ─────────────────────────────────────────────────────────────────────
async def publish_to_telegram(content: str, image_url: str = "") -> tuple[bool, Optional[str]]:
    from backend.config import get_api_key
    token = get_api_key("telegram_token")
    channel_id = get_api_key("telegram_channel_id")

    if not token or not channel_id:
        return False, "مفاتيح Telegram غير مكتملة"

    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            if image_url:
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                payload = {
                    "chat_id": channel_id,
                    "photo": image_url,
                    "caption": content[:1024],
                    "parse_mode": "HTML",
                }
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        return True, None
                    # إذا فشلت الصورة، حاول إرسال نص فقط
                    logger.warning(f"فشل إرسال الصورة لـ Telegram: {data.get('description')}")

            # إرسال نص فقط
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": channel_id,
                "text": content[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return True, None
                return False, data.get("description", "خطأ غير معروف")

    except Exception as exc:
        return False, str(exc)


async def test_telegram() -> dict:
    success, error = await publish_to_telegram("🤖 اختبار النظام — نظام الإحالة يعمل بنجاح ✓")
    return {"success": success, "message": "متصل ✓ — رسالة اختبار أُرسلت" if success else f"خطأ: {error}"}


# ── واتساب (UltraMsg) ──────────────────────────────────────────────────────────
async def publish_to_whatsapp(content: str, image_url: str = "") -> tuple[bool, Optional[str]]:
    from backend.config import get_api_key
    instance_id = get_api_key("ultramsg_instance")
    token = get_api_key("ultramsg_token")
    to_number = get_api_key("whatsapp_number")

    if not all([instance_id, token, to_number]):
        return False, "إعدادات UltraMsg غير مكتملة"

    import aiohttp

    try:
        base_url = f"https://api.ultramsg.com/{instance_id}"
        async with aiohttp.ClientSession() as session:
            if image_url:
                url = f"{base_url}/messages/image"
                payload = {
                    "token": token,
                    "to": to_number,
                    "image": image_url,
                    "caption": content[:1000],
                }
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()
                    if data.get("sent") == "true" or data.get("sent") is True:
                        return True, None

            url = f"{base_url}/messages/chat"
            payload = {"token": token, "to": to_number, "body": content}
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if data.get("sent") == "true" or data.get("sent") is True:
                    return True, None
                return False, str(data)

    except Exception as exc:
        return False, str(exc)


async def test_whatsapp() -> dict:
    success, error = await publish_to_whatsapp("🤖 اختبار النظام — نظام الإحالة يعمل بنجاح ✓")
    return {"success": success, "message": "متصل ✓" if success else f"خطأ: {error}"}


# ── منصة X (Twitter) ──────────────────────────────────────────────────────────
async def publish_to_x(content: str, image_url: str = "") -> tuple[bool, Optional[str]]:
    from backend.config import get_api_key
    api_key = get_api_key("x_api_key")
    api_secret = get_api_key("x_api_secret")
    access_token = get_api_key("x_access_token")
    access_secret = get_api_key("x_access_secret")

    if not all([api_key, api_secret, access_token, access_secret]):
        return False, "مفاتيح X API غير مكتملة"

    try:
        import tweepy

        client = tweepy.AsyncClient(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
        )

        # اقتطع المحتوى بذكاء مع الاحتفاظ بالرابط
        tweet_text = _trim_for_x(content, 280)
        media_ids = []

        if image_url:
            # رفع الصورة عبر API v1.1
            auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
            api_v1 = tweepy.API(auth)
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status == 200:
                            img_bytes = await resp.read()
                            import tempfile, os
                            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                                f.write(img_bytes)
                                tmp_path = f.name
                            media = await asyncio.to_thread(api_v1.media_upload, tmp_path)
                            media_ids = [media.media_id]
                            os.unlink(tmp_path)
            except Exception as img_exc:
                logger.warning(f"فشل رفع الصورة لـ X: {img_exc}")

        kwargs = {"text": tweet_text}
        if media_ids:
            kwargs["media_ids"] = media_ids

        response = await client.create_tweet(**kwargs)
        if response.data:
            return True, None
        return False, "لم يتم إنشاء التغريدة"

    except ImportError:
        return False, "tweepy غير مثبتة"
    except Exception as exc:
        return False, str(exc)


def _trim_for_x(content: str, max_len: int = 280) -> str:
    """اقتطع المحتوى مع الاحتفاظ بالرابط"""
    if len(content) <= max_len:
        return content

    # حاول استخراج الرابط
    import re
    urls = re.findall(r'https?://\S+', content)
    last_url = urls[-1] if urls else ""

    if last_url:
        # احجز مساحة للرابط + مسافة + ...
        url_len = len(last_url)
        trim_target = max_len - url_len - 5
        trimmed = content[:trim_target].rstrip()
        return f"{trimmed}...\n{last_url}"

    return content[:max_len - 3] + "..."


async def test_x_connection() -> dict:
    from backend.config import get_api_key
    api_key = get_api_key("x_api_key")
    if not api_key:
        return {"success": False, "message": "مفاتيح X غير موجودة"}
    return {"success": True, "message": "مفاتيح X موجودة — اختبر النشر يدوياً"}


# ── مساعدات ──────────────────────────────────────────────────────────────────────
def _log_results(product_id: int, results: dict, errors: dict):
    from backend.config import SessionLocal
    from backend.models import PublishLog
    try:
        with SessionLocal() as db:
            for channel, success in results.items():
                log = PublishLog(
                    product_id=product_id,
                    channel=channel,
                    success=success,
                    error_message=errors.get(channel),
                    published_at=datetime.utcnow(),
                )
                db.add(log)
            db.commit()
    except Exception as exc:
        logger.error(f"خطأ في تسجيل نتائج النشر: {exc}")


def _update_product_status(product_id: int, status: str):
    from backend.config import SessionLocal
    from backend.models import Product
    try:
        with SessionLocal() as db:
            product = db.get(Product, product_id)
            if product:
                product.status = status
                if status == "published":
                    product.published_at = datetime.utcnow()
                db.commit()
    except Exception as exc:
        logger.error(f"خطأ في تحديث حالة المنتج: {exc}")
