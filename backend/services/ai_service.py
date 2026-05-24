"""
ai_service.py — توليد محتوى عربي جذاب عبر OpenRouter
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── النماذج المدعومة (OpenRouter فقط) ─────────────────────────────────────────

SUPPORTED_MODELS = {
    # ── مجانية — Gemini أولاً (الأكثر موثوقية) ────────────
    "openrouter/google/gemini-2.0-flash-exp:free": {
        "provider": "openrouter",
        "name": "Gemini 2.0 Flash (مجاني)",
        "speed": "fast",
        "quality": "high",
        "label": "Gemini 2.0 Flash — مجاني / جودة عالية ⭐",
    },
    "openrouter/google/gemini-flash-1.5-8b:free": {
        "provider": "openrouter",
        "name": "Gemini 1.5 Flash 8B (مجاني)",
        "speed": "fastest",
        "quality": "good",
        "label": "Gemini 1.5 Flash 8B — مجاني / سريع جداً",
    },
    "openrouter/google/gemini-flash-1.5:free": {
        "provider": "openrouter",
        "name": "Gemini 1.5 Flash (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Gemini 1.5 Flash — مجاني / جودة جيدة",
    },
    # ── مجانية — أخرى ──────────────────────────────────────
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": {
        "provider": "openrouter",
        "name": "Llama 3.3 70B (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Llama 3.3 70B — مجاني / جودة جيدة",
    },
    "openrouter/deepseek/deepseek-r1:free": {
        "provider": "openrouter",
        "name": "DeepSeek R1 (مجاني)",
        "speed": "medium",
        "quality": "high",
        "label": "DeepSeek R1 — مجاني / جودة عالية",
    },
    "openrouter/deepseek/deepseek-chat:free": {
        "provider": "openrouter",
        "name": "DeepSeek Chat (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "DeepSeek Chat — مجاني / جودة جيدة",
    },
    "openrouter/mistralai/mistral-7b-instruct:free": {
        "provider": "openrouter",
        "name": "Mistral 7B (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Mistral 7B — مجاني / سريع",
    },
    "openrouter/qwen/qwen-2.5-72b-instruct:free": {
        "provider": "openrouter",
        "name": "Qwen 2.5 72B (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Qwen 2.5 72B — مجاني / جودة جيدة",
    },
    # ── مدفوعة ─────────────────────────────────────────────
    "openrouter/google/gemini-2.0-flash-001": {
        "provider": "openrouter",
        "name": "Gemini 2.0 Flash (مدفوع)",
        "speed": "fast",
        "quality": "high",
        "label": "Gemini 2.0 Flash — مدفوع / جودة عالية",
    },
    "openrouter/google/gemini-flash-1.5": {
        "provider": "openrouter",
        "name": "Gemini 1.5 Flash (مدفوع)",
        "speed": "fast",
        "quality": "good",
        "label": "Gemini 1.5 Flash — مدفوع / جودة جيدة",
    },
    "openrouter/meta-llama/llama-3.3-70b-instruct": {
        "provider": "openrouter",
        "name": "Llama 3.3 70B (مدفوع)",
        "speed": "fast",
        "quality": "good",
        "label": "Llama 3.3 70B — مدفوع / جودة جيدة",
    },
    "openrouter/openai/gpt-4o-mini": {
        "provider": "openrouter",
        "name": "GPT-4o Mini",
        "speed": "fast",
        "quality": "good",
        "label": "GPT-4o Mini via OpenRouter — سريع / جودة جيدة",
    },
    "openrouter/openai/gpt-4o": {
        "provider": "openrouter",
        "name": "GPT-4o",
        "speed": "medium",
        "quality": "high",
        "label": "GPT-4o via OpenRouter — متوسط / جودة عالية",
    },
    "openrouter/anthropic/claude-3-haiku": {
        "provider": "openrouter",
        "name": "Claude 3 Haiku",
        "speed": "fast",
        "quality": "good",
        "label": "Claude 3 Haiku via OpenRouter — سريع / جودة جيدة",
    },
    "openrouter/anthropic/claude-3.5-sonnet": {
        "provider": "openrouter",
        "name": "Claude 3.5 Sonnet",
        "speed": "medium",
        "quality": "high",
        "label": "Claude 3.5 Sonnet via OpenRouter — متوسط / جودة عالية",
    },
}

# ربط اسم المزود بمفتاح API المقابل في config
_PROVIDER_KEY_MAP = {
    "openrouter": "openrouter",
}

SYSTEM_PROMPT = """أنت كاتب محتوى تسويقي محترف متخصص في منتجات أمازون للسوق السعودي والخليجي.
مهمتك: كتابة بوست جذاب ومقنع بالعربي لمنصات التواصل الاجتماعي.

قواعد ثابتة:
- ابدأ دائماً بإيموجي مميز يعكس المنتج
- اكتب العنوان بشكل مثير للاهتمام (سطر واحد)
- اذكر السعر الأصلي والحالي والخصم بوضوح
- أضف 3 مميزات رئيسية كنقاط سريعة
- ختم بـ CTA واضح ورابط
- الطول: 150-200 كلمة
- اللهجة: عربية فصحى مبسطة
- لا تكذب أو تبالغ في المميزات"""

USER_PROMPT_TEMPLATE = """اكتب بوست تسويقي لهذا المنتج:

الاسم: {title}
السعر الأصلي: {price_original} ريال
السعر الحالي: {price_current} ريال
الخصم: {discount_pct}%
{rating_line}{offers_block}الرابط: {affiliate_url}

ملاحظات مهمة:
- {rating_instruction}
- {offers_instruction}
- اختم بسطر يوجّه القارئ لقناة العروض على تيليغرام: {telegram_link}

أضف في نهاية البوست: {affiliate_url}"""

FALLBACK_TEMPLATE = """🛒 عرض مميز لا تفوّته!

✨ {title}

💰 السعر الأصلي: {price_original} ريال
🔥 السعر الحالي: {price_current} ريال
📉 خصم {discount_pct}%
{rating_block}{offers_block}
🔗 اطلبه الآن: {affiliate_url}

#تسوق_أونلاين #عروض_أمازون #خصومات{telegram_footer}"""

DEAL_OF_DAY_SYSTEM = """أنت كاتب محتوى تسويقي محترف متخصص في منتجات أمازون للسوق السعودي.
مهمتك: كتابة بوست استثنائي لـ "عرض اليوم" — العرض الأقوى والأبرز يومياً.
هذا ليس منشوراً عادياً، بل العرض الأقوى الذي يجب أن ينتهز المتابع هذه الفرصة فوراً."""

DEAL_OF_DAY_PROMPT = """اكتب بوست "عرض اليوم" لهذا المنتج الاستثنائي:

الاسم: {title}
السعر الأصلي: {price_original} ريال
السعر الحالي: {price_current} ريال
الخصم: {discount_pct}% (توفير {saving} ريال)
{rating_line}{offers_block}
المطلوب:
- 🌟 ابدأ بـ "⭐ عرض اليوم فقط!" بشكل بارز ومميز
- أبرز قيمة التوفير بالريال: "وفّر {saving} ريال اليوم!"
- أضف إحساساً بالعجلة: "العرض محدود - لا تتأخر!"
- اذكر 4 مميزات رئيسية قوية بأسلوب نقاط
- {rating_instruction}
- {offers_instruction}
- اختم بـ CTA قوي وعاجل ووجّه القارئ لقناة العروض: {telegram_link}
- الطول: 200-250 كلمة
- اللهجة: متحمسة لكن مهنية

الرابط في النهاية: {affiliate_url}"""

DEAL_OF_DAY_FALLBACK = """⭐ عرض اليوم فقط!

🔥 {title}

💥 سعر لن تجده مرة أخرى:
💰 كان: {price_original} ريال
✅ الآن: {price_current} ريال فقط!
📉 خصم {discount_pct}% — توفير {saving} ريال!

⏰ العرض محدود — لا تتأخر!
{rating_block}{offers_block}
🛒 اطلبه الآن قبل نفاد الكمية:
{affiliate_url}

#عرض_اليوم #أفضل_عرض #أمازون_السعودية{telegram_footer}"""


# ── اكتشاف المفاتيح المتاحة ───────────────────────────────────────────────────

# نماذج OpenRouter المجانية — Gemini أولاً (الأكثر موثوقية وأسرع استجابة)
_FREE_OPENROUTER_DEFAULTS = [
    "openrouter/google/gemini-2.0-flash-exp:free",
    "openrouter/google/gemini-flash-1.5-8b:free",
    "openrouter/google/gemini-flash-1.5:free",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/deepseek/deepseek-chat:free",
    "openrouter/mistralai/mistral-7b-instruct:free",
    "openrouter/qwen/qwen-2.5-72b-instruct:free",
    "openrouter/deepseek/deepseek-r1:free",
]


def _build_model_priority(custom_system: bool = False) -> list[str]:
    """
    يبني قائمة النماذج الجاهزة بناءً على المفاتيح المضبوطة فعلاً.
    يقبل أي نموذج موجود في SUPPORTED_MODELS طالما مفتاح مزوّده مضبوط.
    """
    from backend.config import get_api_key, get_setting

    primary = get_setting("primary_llm", "")
    saved_list = get_setting("llm_priority", [])

    def key_available(model_id: str) -> bool:
        info = SUPPORTED_MODELS.get(model_id)
        if not info:
            return False
        service = _PROVIDER_KEY_MAP.get(info["provider"], info["provider"])
        return bool(get_api_key(service))

    ordered: list[str] = []

    # ① النموذج الأساسي الذي اختاره المستخدم (يُتجاهل إن لم يعد موجوداً)
    if primary and primary in SUPPORTED_MODELS and key_available(primary):
        ordered.append(primary)

    # ② قائمة الأولوية المحفوظة (يُتجاهل ما لم يعد موجوداً في SUPPORTED_MODELS)
    for mid in (saved_list if isinstance(saved_list, list) else []):
        if mid not in ordered and mid in SUPPORTED_MODELS and key_available(mid):
            ordered.append(mid)

    # ③ أضف دائماً نماذج الـ fallback المجانية التي لم تُضَف بعد
    for mid in _FREE_OPENROUTER_DEFAULTS:
        if mid not in ordered and mid in SUPPORTED_MODELS and key_available(mid):
            ordered.append(mid)

    return ordered


# ── دالة الاستدعاء — OpenRouter فقط ─────────────────────────────────────────


class _RateLimitError(RuntimeError):
    """429 من OpenRouter — يُميّزه generate_content لإضافة تأخير قبل المحاولة التالية."""


async def _call_openrouter(model_id: str, user_prompt: str, system: str = SYSTEM_PROMPT) -> str:
    """
    OpenRouter — بوابة موحّدة.  model_id بصيغة: openrouter/provider/model-name[:free]
    يستخدم httpx مباشرة لتجنّب مشاكل openai SDK مع بعض الإصدارات.
    """
    from backend.config import get_api_key
    api_key = get_api_key("openrouter")
    if not api_key:
        raise ValueError("مفتاح OpenRouter غير موجود")

    actual_model = model_id.removeprefix("openrouter/")

    import httpx
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://affiliate-automation.app",
                "X-Title": "Affiliate Automation System",
                "Content-Type": "application/json",
            },
            json={
                "model": actual_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_prompt},
                ],
                "max_tokens": 1024,
                # يسمح لـ OpenRouter بتجربة مزوّدين بديلين تلقائياً
                "provider": {"allow_fallbacks": True},
            },
        )

    if resp.status_code == 429:
        raise _RateLimitError(f"rate limit 429 — {actual_model}")
    if resp.status_code == 402:
        raise RuntimeError("OpenRouter: رصيد غير كافٍ (402) — تحقق من حسابك")
    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"OpenRouter: استجابة غير صالحة: {resp.text[:100]}")

    # OpenRouter يُرجع أحياناً {"error": {...}} حتى مع status 200
    if "error" in data:
        err = data["error"]
        code = err.get("code") or 0
        msg = err.get("message", str(err))
        if code == 429 or "rate" in str(msg).lower():
            raise _RateLimitError(f"rate limit (error body) — {actual_model}")
        raise RuntimeError(f"OpenRouter error: {msg[:120]}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenRouter: لا توجد choices — {str(data)[:150]}")

    content = (choices[0].get("message") or {}).get("content") or ""
    if not content:
        finish = choices[0].get("finish_reason", "unknown")
        raise RuntimeError(f"OpenRouter: محتوى فارغ (finish_reason={finish})")

    return content


_PROVIDER_CALLERS = {
    "openrouter": _call_openrouter,
}


# ── الدوال الرئيسية ───────────────────────────────────────────────────────────────

def _short_error(exc: Exception, max_len: int = 60) -> str:
    """اختصر رسالة الخطأ لتناسب عمود ai_model_used (VARCHAR(100))"""
    msg = str(exc).strip().replace("\n", " ")
    return msg[:max_len] + "…" if len(msg) > max_len else msg


def _telegram_channel_link() -> str:
    """يستخرج رابط قناة تيليغرام من الإعدادات (للعرض داخل المحتوى)."""
    try:
        from backend.config import get_setting
        footer = get_setting("telegram_footer", "") or ""
        import re
        m = re.search(r"https?://t\.me/[^\s]+", footer)
        if m:
            return m.group(0)
    except Exception:
        pass
    return ""


def _telegram_footer_block() -> str:
    """نص footer كامل (مع سطر فاصل) ليُلحق بنهاية المنشور — أو فارغ إن لم يُضبط."""
    try:
        from backend.config import get_setting
        footer = (get_setting("telegram_footer", "") or "").rstrip()
        if footer:
            if not footer.startswith("\n"):
                footer = "\n\n" + footer.lstrip("\n")
            return footer
    except Exception:
        pass
    return ""


def _rating_line(product: dict) -> str:
    """سطر التقييم للـ prompt — يُحذف إذا عدد التقييمات صفر."""
    rc = int(product.get("rating_count") or 0)
    rating = product.get("rating") or 0
    if rc <= 0 or not rating:
        return ""
    return f"التقييم: {rating}/5 ({rc} تقييم)\n"


def _rating_block(product: dict) -> str:
    """كتلة التقييم للـ fallback template — تُحذف إذا عدد التقييمات صفر."""
    rc = int(product.get("rating_count") or 0)
    rating = product.get("rating") or 0
    if rc <= 0 or not rating:
        return ""
    return f"\n⭐ التقييم: {rating}/5 ({rc} تقييم)\n"


def _offers_block_for_prompt(product: dict) -> str:
    offers = product.get("offers") or []
    if not offers:
        return ""
    lines = "\n".join(f"- {o}" for o in offers if o)
    return f"عروض إضافية متاحة:\n{lines}\n"


def _offers_block_for_fallback(product: dict) -> str:
    offers = product.get("offers") or []
    if not offers:
        return ""
    lines = "\n".join(f"💳 {o}" for o in offers if o)
    return f"\n{lines}\n"


def _rating_instruction(product: dict) -> str:
    rc = int(product.get("rating_count") or 0)
    if rc <= 0:
        return "لا تذكر التقييم في البوست (عدد المراجعات صفر)"
    return "اذكر التقييم باختصار في مكان مناسب"


def _offers_instruction(product: dict) -> str:
    offers = product.get("offers") or []
    if not offers:
        return "لا توجد عروض إضافية للذكر"
    return "اذكر العروض الإضافية أعلاه (مثل سعر برايم أو خصم البنوك) إن كانت موجودة"


# ── Timeout + Cache + Concurrency ─────────────────────────────────────────────
_PER_MODEL_TIMEOUT    = 45.0   # timeout عام لكل نموذج
_GEMINI_TIMEOUT       = 45.0   # Gemini سريع لكن أحياناً يحتاج 40s عند cold start
_THINKING_MODEL_TIMEOUT = 90.0 # نماذج التفكير (deepseek-r1) تحتاج وقتاً أطول
_THINKING_KEYWORDS = ("deepseek-r1", "o1-", "reasoning", "think")
_GEMINI_KEYWORDS   = ("gemini",)
_AI_CACHE: dict[tuple, tuple[str, str, float]] = {}
_AI_CACHE_TTL = 24 * 3600
_AI_CACHE_MAX = 500
_AI_SEMAPHORE = asyncio.Semaphore(3)


def _model_timeout(model_id: str) -> float:
    """يُرجع timeout مناسب حسب نوع النموذج."""
    ml = model_id.lower()
    if any(k in ml for k in _THINKING_KEYWORDS):
        return _THINKING_MODEL_TIMEOUT
    if any(k in ml for k in _GEMINI_KEYWORDS):
        return _GEMINI_TIMEOUT
    return _PER_MODEL_TIMEOUT


def _cache_get(asin: str, kind: str) -> Optional[tuple[str, str]]:
    if not asin:
        return None
    import time as _t
    entry = _AI_CACHE.get((asin, kind))
    if not entry:
        return None
    content, model_used, ts = entry
    if _t.time() - ts > _AI_CACHE_TTL:
        _AI_CACHE.pop((asin, kind), None)
        return None
    return content, model_used


def _cache_set(asin: str, kind: str, content: str, model_used: str) -> None:
    if not asin:
        return
    import time as _t
    if len(_AI_CACHE) >= _AI_CACHE_MAX:
        # تنظيف بسيط — احذف 50 إدخال أقدم
        for k in list(_AI_CACHE.keys())[:50]:
            _AI_CACHE.pop(k, None)
    _AI_CACHE[(asin, kind)] = (content, model_used, _t.time())


async def generate_content(product: dict, llm: str = "auto") -> tuple[str, str]:
    """
    يولّد محتوى عربي للمنتج.
    يُرجع: (النص المولَّد, اسم النموذج المستخدم)
    عند الفشل: يرجع القالب الثابت مع سبب الفشل في model_used للتشخيص.
    """
    # كاش حسب ASIN — يتفادى إعادة التوليد لنفس المنتج
    asin = product.get("asin", "") or ""
    if llm == "auto":
        cached = _cache_get(asin, "post")
        if cached:
            logger.info(f"AI cache hit (post): {asin}")
            return cached[0], f"{cached[1]} (cached)"
    user_prompt = USER_PROMPT_TEMPLATE.format(
        title=product.get("title", ""),
        price_original=product.get("price_original", 0),
        price_current=product.get("price_current", 0),
        discount_pct=product.get("discount_pct", 0),
        rating_line=_rating_line(product),
        offers_block=_offers_block_for_prompt(product),
        rating_instruction=_rating_instruction(product),
        offers_instruction=_offers_instruction(product),
        telegram_link=_telegram_channel_link() or "(لم تُضبط)",
        affiliate_url=product.get("affiliate_url", ""),
    )

    models_to_try = _build_model_priority() if llm == "auto" else [llm]

    if not models_to_try:
        logger.warning("لا توجد نماذج AI مضبوطة — أضف مفتاح API من صفحة الإعدادات")
        return _make_fallback(product), "fallback: لا يوجد مفتاح AI مضبوط"

    last_error: Optional[Exception] = None
    last_model: Optional[str] = None
    for model_id in models_to_try:
        if model_id not in SUPPORTED_MODELS:
            logger.warning(f"نموذج غير معروف: {model_id}")
            continue

        provider = SUPPORTED_MODELS[model_id]["provider"]
        caller = _PROVIDER_CALLERS.get(provider)
        if not caller:
            continue

        last_model = model_id
        _timeout = _model_timeout(model_id)
        try:
            logger.info(f"توليد المحتوى بـ {model_id} (timeout={_timeout}s)")
            async with _AI_SEMAPHORE:
                content = await asyncio.wait_for(
                    caller(model_id, user_prompt),
                    timeout=_timeout,
                )
            if content and len(content.strip()) > 30:
                logger.info(f"نجح {model_id} ✓")
                if llm == "auto":
                    _cache_set(asin, "post", content, model_id)
                return content, model_id
            last_error = RuntimeError(f"محتوى قصير/فارغ (طول={len(content) if content else 0})")
            logger.warning(f"{model_id}: {last_error}")
        except asyncio.TimeoutError:
            last_error = RuntimeError(f"timeout بعد {_timeout}s")
            logger.warning(f"تجاوز الوقت {model_id} — ننتقل للنموذج التالي")
        except _RateLimitError as exc:
            last_error = exc
            logger.warning(f"rate limit 429 لـ {model_id} — انتظار ثانية ثم المحاولة التالية")
            await asyncio.sleep(1)
        except Exception as exc:
            last_error = exc
            logger.warning(f"فشل {model_id}: {exc}")

    logger.error(f"فشلت كل النماذج ({len(models_to_try)} محاولة) — آخر خطأ: {last_error}")
    err_label = f"fallback ({last_model}): {_short_error(last_error)}" if last_error else "fallback: فشل غير معروف"
    return _make_fallback(product), err_label


async def call_ai(prompt: str, system: str = "", model_id: str = "auto") -> str:
    """
    استدعاء AI بـ prompt مخصص لمهام مثل اختيار المنتجات.
    يُرجع: النص الخام من النموذج.
    """
    sys = system or "أنت مساعد ذكي مفيد."

    models_to_try = _build_model_priority() if model_id == "auto" else [model_id]

    if not models_to_try:
        raise RuntimeError("لا توجد نماذج AI مضبوطة — أضف مفتاح API من الإعدادات")

    for mid in models_to_try:
        if mid not in SUPPORTED_MODELS:
            continue
        provider = SUPPORTED_MODELS[mid]["provider"]
        caller = _PROVIDER_CALLERS.get(provider)
        if not caller:
            continue
        try:
            async with _AI_SEMAPHORE:
                result = await asyncio.wait_for(
                    caller(mid, prompt, system=sys),
                    timeout=_PER_MODEL_TIMEOUT,
                )
            if result and len(result.strip()) > 5:
                return result
        except asyncio.TimeoutError:
            logger.warning(f"call_ai تجاوز الوقت مع {mid} ({_PER_MODEL_TIMEOUT}s)")
            continue
        except Exception as exc:
            logger.warning(f"call_ai فشل مع {mid}: {exc}")
            continue

    raise RuntimeError("جميع نماذج AI غير متاحة أو مفاتيحها غير مضبوطة")


def _make_fallback(product: dict) -> str:
    return FALLBACK_TEMPLATE.format(
        title=product.get("title", "منتج مميز"),
        price_original=product.get("price_original", 0),
        price_current=product.get("price_current", 0),
        discount_pct=product.get("discount_pct", 0),
        rating_block=_rating_block(product),
        offers_block=_offers_block_for_fallback(product),
        affiliate_url=product.get("affiliate_url", ""),
        telegram_footer=_telegram_footer_block(),
    )


async def generate_deal_of_day_content(product: dict, llm: str = "auto") -> tuple[str, str]:
    """
    يولّد محتوى خاص بـ "عرض اليوم" — أكثر إثارة وعجلة من البوست العادي.
    """
    saving = round((product.get("price_original", 0) or 0) - (product.get("price_current", 0) or 0))

    user_prompt = DEAL_OF_DAY_PROMPT.format(
        title=product.get("title", ""),
        price_original=product.get("price_original", 0),
        price_current=product.get("price_current", 0),
        discount_pct=product.get("discount_pct", 0),
        saving=saving,
        rating_line=_rating_line(product),
        offers_block=_offers_block_for_prompt(product),
        rating_instruction=_rating_instruction(product),
        offers_instruction=_offers_instruction(product),
        telegram_link=_telegram_channel_link() or "(لم تُضبط)",
        affiliate_url=product.get("affiliate_url", ""),
    )

    models_to_try = _build_model_priority() if llm == "auto" else [llm]

    if not models_to_try:
        return _make_deal_of_day_fallback(product), "fallback: لا يوجد مفتاح AI مضبوط"

    asin = product.get("asin", "") or ""
    if llm == "auto":
        cached = _cache_get(asin, "deal")
        if cached:
            return cached[0], f"{cached[1]} (cached)"

    last_error: Optional[Exception] = None
    last_model: Optional[str] = None
    for model_id in models_to_try:
        if model_id not in SUPPORTED_MODELS:
            continue
        provider = SUPPORTED_MODELS[model_id]["provider"]
        caller = _PROVIDER_CALLERS.get(provider)
        if not caller:
            continue
        last_model = model_id
        _timeout = _model_timeout(model_id)
        try:
            logger.info(f"توليد محتوى عرض اليوم بـ {model_id} (timeout={_timeout}s)")
            async with _AI_SEMAPHORE:
                content = await asyncio.wait_for(
                    caller(model_id, user_prompt, system=DEAL_OF_DAY_SYSTEM),
                    timeout=_timeout,
                )
            if content and len(content.strip()) > 30:
                if llm == "auto":
                    _cache_set(asin, "deal", content, model_id)
                return content, model_id
            last_error = RuntimeError(f"محتوى قصير/فارغ")
        except asyncio.TimeoutError:
            last_error = RuntimeError(f"timeout بعد {_timeout}s")
            logger.warning(f"تجاوز الوقت {model_id} (عرض اليوم)")
        except _RateLimitError as exc:
            last_error = exc
            logger.warning(f"rate limit 429 لـ {model_id} (عرض اليوم) — انتظار ثانية")
            await asyncio.sleep(1)
        except Exception as exc:
            last_error = exc
            logger.warning(f"فشل {model_id} (عرض اليوم): {exc}")

    logger.error(f"فشلت كل النماذج لعرض اليوم — آخر خطأ: {last_error}")
    err_label = f"fallback ({last_model}): {_short_error(last_error)}" if last_error else "fallback: فشل غير معروف"
    return _make_deal_of_day_fallback(product), err_label


def _make_deal_of_day_fallback(product: dict) -> str:
    saving = round((product.get("price_original", 0) or 0) - (product.get("price_current", 0) or 0))
    return DEAL_OF_DAY_FALLBACK.format(
        title=product.get("title", "منتج مميز"),
        price_original=product.get("price_original", 0),
        price_current=product.get("price_current", 0),
        discount_pct=product.get("discount_pct", 0),
        saving=saving,
        rating_block=_rating_block(product),
        offers_block=_offers_block_for_fallback(product),
        affiliate_url=product.get("affiliate_url", ""),
        telegram_footer=_telegram_footer_block(),
    )


# ── اختبار نموذج معيّن ─────────────────────────────────────────────────────────

async def test_model(model_id: str) -> dict:
    sample = {
        "title": "سماعات سوني اللاسلكية WH-1000XM5",
        "price_original": 1299.0,
        "price_current": 849.0,
        "discount_pct": 35,
        "rating": 4.7,
        "rating_count": 5823,
        "affiliate_url": "https://www.amazon.sa/dp/B09XS7JWHH?tag=demo-21",
    }
    try:
        content, used_model = await generate_content(sample, llm=model_id)
        if used_model.startswith("fallback"):
            return {"success": False, "message": f"فشل التوليد — {used_model}"}
        return {
            "success": True,
            "message": f"متصل ✓ — {used_model} جاهز",
            "preview": content[:150],
        }
    except Exception as exc:
        return {"success": False, "message": str(exc)}


async def test_generate_live(model_id: str, product: Optional[dict] = None) -> dict:
    """
    اختبار حي لنموذج معيّن — يُرجع تفاصيل كاملة عن النجاح أو سبب الفشل الدقيق.
    """
    import time as _t
    from backend.config import get_api_key

    sample = product or {
        "title": "سماعات سوني اللاسلكية WH-1000XM5",
        "price_original": 1299.0,
        "price_current": 849.0,
        "discount_pct": 35,
        "rating": 4.7,
        "rating_count": 5823,
        "affiliate_url": "https://www.amazon.sa/dp/B09XS7JWHH?tag=demo-21",
    }

    # التحقق من وجود النموذج
    if model_id not in SUPPORTED_MODELS:
        return {
            "success": False,
            "stage": "validation",
            "error": f"النموذج {model_id!r} غير موجود في القائمة المدعومة",
        }

    info = SUPPORTED_MODELS[model_id]
    provider = info["provider"]
    service = _PROVIDER_KEY_MAP.get(provider, provider)
    api_key = get_api_key(service)

    if not api_key:
        return {
            "success": False,
            "stage": "api_key",
            "error": f"مفتاح API للمزوّد '{provider}' غير موجود — أضفه من صفحة مفاتيح API",
            "provider": provider,
            "service_key": service,
        }

    caller = _PROVIDER_CALLERS.get(provider)
    if not caller:
        return {
            "success": False,
            "stage": "caller",
            "error": f"لا يوجد handler للمزوّد '{provider}'",
        }

    user_prompt = USER_PROMPT_TEMPLATE.format(
        title=sample.get("title", ""),
        price_original=sample.get("price_original", 0),
        price_current=sample.get("price_current", 0),
        discount_pct=sample.get("discount_pct", 0),
        rating_line=_rating_line(sample),
        offers_block=_offers_block_for_prompt(sample),
        rating_instruction=_rating_instruction(sample),
        offers_instruction=_offers_instruction(sample),
        telegram_link="",
        affiliate_url=sample.get("affiliate_url", ""),
    )

    _timeout = _model_timeout(model_id)
    t0 = _t.time()
    try:
        async with _AI_SEMAPHORE:
            content = await asyncio.wait_for(
                caller(model_id, user_prompt),
                timeout=_timeout,
            )
        elapsed = round(_t.time() - t0, 2)

        if not content or len(content.strip()) < 30:
            return {
                "success": False,
                "stage": "content_check",
                "error": f"المحتوى المُولَّد قصير جداً أو فارغ (طول={len(content) if content else 0})",
                "raw": content or "",
                "elapsed_s": elapsed,
            }

        return {
            "success": True,
            "model_id": model_id,
            "provider": provider,
            "elapsed_s": elapsed,
            "content_length": len(content),
            "preview": content[:300],
            "full_content": content,
        }

    except asyncio.TimeoutError:
        return {
            "success": False,
            "stage": "timeout",
            "error": f"انتهت المهلة الزمنية بعد {_timeout}ث — النموذج بطيء أو غير متاح",
            "elapsed_s": round(_t.time() - t0, 2),
        }
    except Exception as exc:
        return {
            "success": False,
            "stage": "api_call",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "elapsed_s": round(_t.time() - t0, 2),
        }


async def get_available_models() -> list:
    return [{"id": k, **v} for k, v in SUPPORTED_MODELS.items()]


async def diagnose_ai() -> dict:
    """
    فحص شامل لإعدادات الذكاء الاصطناعي. يُرجع:
      - configured_providers: المزوّدون الذين لديهم مفاتيح
      - available_models: النماذج التي يمكن استخدامها فعلاً
      - priority_order: الترتيب الفعلي عند التوليد
      - test_result: نتيجة استدعاء أول نموذج متاح بمدخل اختباري
    """
    from backend.config import get_api_key, is_key_from_env

    providers = list({info["provider"] for info in SUPPORTED_MODELS.values()})
    configured = []
    for prov in providers:
        service = _PROVIDER_KEY_MAP.get(prov, prov)
        key = get_api_key(service)
        if key:
            configured.append({
                "provider": prov,
                "source": "env" if is_key_from_env(service) else "database",
                "key_preview": key[:4] + "…" + key[-4:] if len(key) > 8 else "••••",
            })

    priority = _build_model_priority()
    available = [
        {"id": mid, **SUPPORTED_MODELS[mid]} for mid in priority if mid in SUPPORTED_MODELS
    ]

    test_result: dict = {"attempted": False}
    if priority:
        first = priority[0]
        test_result["attempted"] = True
        test_result["model"] = first
        try:
            sample = {
                "title": "اختبار",
                "price_original": 100,
                "price_current": 70,
                "discount_pct": 30,
                "rating": 4.5,
                "rating_count": 100,
                "affiliate_url": "https://example.com",
            }
            content, used = await generate_content(sample, llm=first)
            if used.startswith("fallback"):
                test_result["success"] = False
                test_result["error"] = used
            else:
                test_result["success"] = True
                test_result["model_used"] = used
                test_result["preview"] = content[:120]
        except Exception as exc:
            test_result["success"] = False
            test_result["error"] = str(exc)

    return {
        "configured_providers": configured,
        "available_models": available,
        "priority_order": priority,
        "test_result": test_result,
        "healthy": bool(priority) and test_result.get("success", False),
    }
