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
    from backend.models import Product

    def _load_product():
        with SessionLocal() as db:
            p = db.get(Product, product_id)
            if not p:
                return None, None
            return p.ai_content or "", p.image_url or ""

    content, image_url = await asyncio.to_thread(_load_product)
    if content is None:
        return {"success": False, "error": "المنتج غير موجود"}

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
    post_ids = {}  # channel → post_id

    if tasks:
        channel_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for channel, result in zip(tasks.keys(), channel_results):
            if isinstance(result, Exception):
                results[channel] = False
                errors[channel] = str(result)
            else:
                # result = (success, error_msg, post_id) أو (success, error_msg)
                if isinstance(result, tuple) and len(result) >= 3:
                    success, error_msg, post_id = result[0], result[1], result[2]
                elif isinstance(result, tuple) and len(result) == 2:
                    success, error_msg = result
                    post_id = None
                else:
                    success, error_msg, post_id = bool(result), None, None

                results[channel] = success
                if error_msg:
                    errors[channel] = error_msg
                if post_id:
                    post_ids[channel] = post_id

    await asyncio.to_thread(_log_results, product_id, results, errors, post_ids)

    all_success = any(results.values()) if results else False
    await asyncio.to_thread(_update_product_status, product_id, "published" if all_success else "failed", post_ids)

    return {**results, "errors": errors, "success": all_success}


# ── تيليغرام ─────────────────────────────────────────────────────────────────────
async def publish_to_telegram(content: str, image_url: str = "") -> tuple:
    from backend.config import get_api_key, get_setting
    token = get_api_key("telegram_token")
    channel_id = get_api_key("telegram_channel_id")

    if not token or not channel_id:
        return False, "مفاتيح Telegram غير مكتملة", None

    # ① ألحق التوقيع (إن لم يكن موجوداً)
    footer = get_setting("telegram_footer", "") or ""
    if footer and footer.strip() not in (content or ""):
        content = (content or "") + footer

    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            if image_url:
                # ② إن كان المحتوى يتجاوز 1024 → قسّم: caption + رسالة الـ footer منفصلة
                caption_limit = 1024
                send_footer_separately = False
                caption = content
                if len(content) > caption_limit and footer:
                    # حاول إرسال الـ caption بدون الـ footer إن كان مع الـ footer زائد
                    body_only = content[: -len(footer)] if content.endswith(footer) else content
                    if len(body_only) <= caption_limit:
                        caption = body_only
                        send_footer_separately = True
                    else:
                        caption = content[:caption_limit]
                else:
                    caption = content[:caption_limit]

                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                payload = {
                    "chat_id": channel_id,
                    "photo": image_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                }
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        msg_id = str(data.get("result", {}).get("message_id", ""))
                        # ③ أرسل الـ footer كرسالة منفصلة إن لزم
                        if send_footer_separately:
                            try:
                                await session.post(
                                    f"https://api.telegram.org/bot{token}/sendMessage",
                                    json={
                                        "chat_id": channel_id,
                                        "text": footer.strip(),
                                        "parse_mode": "HTML",
                                        "disable_web_page_preview": True,
                                    },
                                    timeout=aiohttp.ClientTimeout(total=10),
                                )
                            except Exception as fexc:
                                logger.warning(f"فشل إرسال footer منفصل: {fexc}")
                        return True, None, msg_id
                    logger.warning(f"فشل إرسال الصورة لـ Telegram: {data.get('description')}")

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
                    msg_id = str(data.get("result", {}).get("message_id", ""))
                    return True, None, msg_id
                return False, data.get("description", "خطأ غير معروف"), None

    except Exception as exc:
        return False, str(exc), None


async def test_telegram() -> dict:
    success, error, _ = await publish_to_telegram("🤖 اختبار النظام — نظام الإحالة يعمل بنجاح ✓")
    return {"success": success, "message": "متصل ✓ — رسالة اختبار أُرسلت" if success else f"خطأ: {error}"}


# ── واتساب (UltraMsg) ──────────────────────────────────────────────────────────
async def publish_to_whatsapp(content: str, image_url: str = "") -> tuple:
    from backend.config import get_api_key
    instance_id = get_api_key("ultramsg_instance")
    token = get_api_key("ultramsg_token")
    to_number = get_api_key("whatsapp_number")

    if not all([instance_id, token, to_number]):
        return False, "إعدادات UltraMsg غير مكتملة", None

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
                        return True, None, None

            url = f"{base_url}/messages/chat"
            payload = {"token": token, "to": to_number, "body": content}
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if data.get("sent") == "true" or data.get("sent") is True:
                    return True, None, None
                return False, str(data), None

    except Exception as exc:
        return False, str(exc), None


async def test_whatsapp() -> dict:
    success, error, _ = await publish_to_whatsapp("🤖 اختبار النظام — نظام الإحالة يعمل بنجاح ✓")
    return {"success": success, "message": "متصل ✓" if success else f"خطأ: {error}"}


# ── منصة X (Twitter) ──────────────────────────────────────────────────────────
async def publish_to_x(content: str, image_url: str = "") -> tuple:
    from backend.config import get_api_key
    api_key = get_api_key("x_api_key")
    api_secret = get_api_key("x_api_secret")
    access_token = get_api_key("x_access_token")
    access_secret = get_api_key("x_access_secret")

    if not all([api_key, api_secret, access_token, access_secret]):
        return False, "مفاتيح X API غير مكتملة", None

    try:
        import tweepy

        client = tweepy.AsyncClient(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
        )

        tweet_text = _trim_for_x(content, 280)
        media_ids = []

        if image_url:
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
            tweet_id = str(response.data.get("id", ""))
            return True, None, tweet_id
        return False, "لم يتم إنشاء التغريدة", None

    except ImportError:
        return False, "tweepy غير مثبتة", None
    except Exception as exc:
        return False, str(exc), None


def _trim_for_x(content: str, max_len: int = 280) -> str:
    if len(content) <= max_len:
        return content
    import re
    urls = re.findall(r'https?://\S+', content)
    last_url = urls[-1] if urls else ""
    if last_url:
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
def _log_results(product_id: int, results: dict, errors: dict, post_ids: dict = None):
    from backend.config import SessionLocal
    from backend.models import PublishLog
    post_ids = post_ids or {}
    try:
        with SessionLocal() as db:
            for channel, success in results.items():
                log = PublishLog(
                    product_id=product_id,
                    channel=channel,
                    success=success,
                    post_id=post_ids.get(channel),
                    error_message=errors.get(channel),
                    published_at=datetime.utcnow(),
                )
                db.add(log)
            db.commit()
    except Exception as exc:
        logger.error(f"خطأ في تسجيل نتائج النشر: {exc}")


def _update_product_status(product_id: int, status: str, post_ids: dict = None):
    from backend.config import SessionLocal
    from backend.models import Product
    post_ids = post_ids or {}
    try:
        with SessionLocal() as db:
            product = db.get(Product, product_id)
            if product:
                product.status = status
                if status == "published":
                    product.published_at = datetime.utcnow()
                if post_ids.get("telegram"):
                    product.telegram_post_id = post_ids["telegram"]
                if post_ids.get("x"):
                    product.x_tweet_id = post_ids["x"]
                db.commit()
    except Exception as exc:
        logger.error(f"خطأ في تحديث حالة المنتج: {exc}")
