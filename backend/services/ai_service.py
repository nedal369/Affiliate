"""
ai_service.py — توليد محتوى عربي جذاب باستخدام متعدد الـ LLM
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── النماذج المدعومة ────────────────────────────────────────────────────────────

SUPPORTED_MODELS = {
    # ── Anthropic ──────────────────────────────────────────
    "claude-haiku-4-5": {
        "provider": "anthropic",
        "name": "Claude Haiku 4.5",
        "speed": "fast",
        "quality": "good",
        "label": "Claude Haiku 4.5 — سريع / جودة جيدة",
    },
    "claude-sonnet-4-5": {
        "provider": "anthropic",
        "name": "Claude Sonnet 4.5",
        "speed": "medium",
        "quality": "high",
        "label": "Claude Sonnet 4.5 — متوسط / جودة عالية",
    },
    "claude-opus-4-5": {
        "provider": "anthropic",
        "name": "Claude Opus 4.5",
        "speed": "slow",
        "quality": "highest",
        "label": "Claude Opus 4.5 — بطيء / جودة قصوى",
    },
    # ── OpenAI ─────────────────────────────────────────────
    "gpt-4o-mini": {
        "provider": "openai",
        "name": "GPT-4o Mini",
        "speed": "fast",
        "quality": "good",
        "label": "GPT-4o Mini — سريع / جودة جيدة",
    },
    "gpt-4o": {
        "provider": "openai",
        "name": "GPT-4o",
        "speed": "medium",
        "quality": "high",
        "label": "GPT-4o — متوسط / جودة عالية",
    },
    # ── Groq (مجاني وسريع جداً) ────────────────────────────
    "llama-3.3-70b-versatile": {
        "provider": "groq",
        "name": "Llama 3.3 70B (Groq)",
        "speed": "fastest",
        "quality": "good",
        "label": "Llama 3.3 70B Groq — الأسرع / جودة جيدة",
    },
    # ── OpenRouter — مفتاح واحد لمئات النماذج ─────────────
    # نماذج مجانية موثوقة (مُرتَّبة حسب الأولوية)
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": {
        "provider": "openrouter",
        "name": "Llama 3.3 70B (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Llama 3.3 70B via OpenRouter — مجاني / جودة جيدة",
    },
    "openrouter/deepseek/deepseek-r1:free": {
        "provider": "openrouter",
        "name": "DeepSeek R1 (مجاني)",
        "speed": "medium",
        "quality": "high",
        "label": "DeepSeek R1 via OpenRouter — مجاني / جودة عالية",
    },
    "openrouter/deepseek/deepseek-chat:free": {
        "provider": "openrouter",
        "name": "DeepSeek Chat (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "DeepSeek Chat via OpenRouter — مجاني / جودة جيدة",
    },
    "openrouter/qwen/qwen-2.5-72b-instruct:free": {
        "provider": "openrouter",
        "name": "Qwen 2.5 72B (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Qwen 2.5 72B via OpenRouter — مجاني / جودة جيدة",
    },
    "openrouter/google/gemini-2.0-flash-001": {
        "provider": "openrouter",
        "name": "Gemini 2.0 Flash (OpenRouter)",
        "speed": "fast",
        "quality": "high",
        "label": "Gemini 2.0 Flash via OpenRouter — سريع / جودة عالية",
    },
    "openrouter/google/gemini-flash-1.5": {
        "provider": "openrouter",
        "name": "Gemini 1.5 Flash (OpenRouter)",
        "speed": "fast",
        "quality": "good",
        "label": "Gemini 1.5 Flash via OpenRouter — سريع / جودة جيدة",
    },
    "openrouter/meta-llama/llama-3.3-70b-instruct": {
        "provider": "openrouter",
        "name": "Llama 3.3 70B (OpenRouter مدفوع)",
        "speed": "fast",
        "quality": "good",
        "label": "Llama 3.3 70B via OpenRouter — مدفوع / جودة جيدة",
    },
    "openrouter/openai/gpt-4o-mini": {
        "provider": "openrouter",
        "name": "GPT-4o Mini (OpenRouter)",
        "speed": "fast",
        "quality": "good",
        "label": "GPT-4o Mini via OpenRouter — سريع / جودة جيدة",
    },
    "openrouter/anthropic/claude-3-haiku": {
        "provider": "openrouter",
        "name": "Claude 3 Haiku (OpenRouter)",
        "speed": "fast",
        "quality": "good",
        "label": "Claude 3 Haiku via OpenRouter — سريع / جودة جيدة",
    },
    # ── Google مباشر ────────────────────────────────────────
    "gemini-1.5-flash": {
        "provider": "google",
        "name": "Gemini 1.5 Flash",
        "speed": "fast",
        "quality": "good",
        "label": "Gemini 1.5 Flash — سريع / جودة جيدة",
    },
    "gemini-1.5-pro": {
        "provider": "google",
        "name": "Gemini 1.5 Pro",
        "speed": "medium",
        "quality": "high",
        "label": "Gemini 1.5 Pro — متوسط / جودة عالية",
    },
}

# ربط اسم المزود بمفتاح API المقابل في config
_PROVIDER_KEY_MAP = {
    "anthropic":  "anthropic",
    "openai":     "openai",
    "google":     "google",
    "groq":       "groq",
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

# نماذج OpenRouter المجانية — تُستخدم كـ fallback افتراضي عند عدم ضبط أي شيء
_FREE_OPENROUTER_DEFAULTS = [
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/deepseek/deepseek-r1:free",
    "openrouter/deepseek/deepseek-chat:free",
    "openrouter/qwen/qwen-2.5-72b-instruct:free",
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

    # ① النموذج الأساسي الذي اختاره المستخدم — أولوية قصوى إذا مفتاحه موجود
    if primary and primary in SUPPORTED_MODELS and key_available(primary):
        ordered.append(primary)

    # ② قائمة الأولوية المحفوظة كـ fallback
    for mid in (saved_list if isinstance(saved_list, list) else []):
        if mid not in ordered and mid in SUPPORTED_MODELS and key_available(mid):
            ordered.append(mid)

    # ③ إذا لم يُضبط شيء — أضف كل نماذج OpenRouter المجانية المتاحة بترتيب ثابت
    if not ordered:
        for mid in _FREE_OPENROUTER_DEFAULTS:
            if mid in SUPPORTED_MODELS and key_available(mid):
                ordered.append(mid)

    return ordered


# ── دوال الاستدعاء لكل مزوّد ────────────────────────────────────────────────────

async def _call_anthropic(model_id: str, user_prompt: str, system: str = SYSTEM_PROMPT) -> str:
    from backend.config import get_api_key
    api_key = get_api_key("anthropic")
    if not api_key:
        raise ValueError("مفتاح Anthropic غير موجود")
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model=model_id,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


async def _call_openai(model_id: str, user_prompt: str, system: str = SYSTEM_PROMPT) -> str:
    from backend.config import get_api_key
    api_key = get_api_key("openai")
    if not api_key:
        raise ValueError("مفتاح OpenAI غير موجود")
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1024,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI returned empty content")
    return content


_GEMINI_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
]

_GEMINI_FINISH_REASONS = {0: "UNSPECIFIED", 1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER"}


async def _call_gemini(model_id: str, system_prompt: str, user_prompt: str) -> str:
    from backend.config import get_api_key
    api_key = get_api_key("google")
    if not api_key:
        raise ValueError("مفتاح Google Gemini غير موجود")
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_id,
        system_instruction=system_prompt,
        safety_settings=_GEMINI_SAFETY_SETTINGS,
    )
    response = await asyncio.to_thread(model.generate_content, user_prompt)

    if not response:
        raise RuntimeError("Gemini returned None response")

    pf = getattr(response, "prompt_feedback", None)
    if pf:
        block_reason = getattr(pf, "block_reason", None)
        if block_reason:
            raise RuntimeError(f"Gemini prompt blocked: {block_reason}")

    candidates = getattr(response, "candidates", None)
    if not candidates:
        raise RuntimeError("Gemini returned no candidates (likely blocked)")

    candidate = candidates[0]
    finish_reason = getattr(candidate, "finish_reason", None)
    fr_value = int(finish_reason) if finish_reason is not None else 0
    fr_name = _GEMINI_FINISH_REASONS.get(fr_value, str(finish_reason))

    if fr_value not in (1, 2):
        raise RuntimeError(f"Gemini blocked content (finish_reason={fr_name})")

    text = ""
    try:
        text = response.text or ""
    except Exception as e:
        try:
            parts = getattr(candidate.content, "parts", []) or []
            text = "".join(getattr(p, "text", "") for p in parts)
        except Exception:
            raise RuntimeError(f"Gemini text extraction failed: {e}")

    if not text or len(text.strip()) < 10:
        raise RuntimeError(f"Gemini returned empty/short text (finish={fr_name})")

    return text


async def _call_google(model_id: str, user_prompt: str, system: str = SYSTEM_PROMPT) -> str:
    return await _call_gemini(model_id, system, user_prompt)


async def _call_groq(model_id: str, user_prompt: str, system: str = SYSTEM_PROMPT) -> str:
    from backend.config import get_api_key
    api_key = get_api_key("groq")
    if not api_key:
        raise ValueError("مفتاح Groq غير موجود")
    from groq import AsyncGroq
    client = AsyncGroq(api_key=api_key)
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1024,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Groq returned empty content")
    return content


async def _call_openrouter(model_id: str, user_prompt: str, system: str = SYSTEM_PROMPT) -> str:
    """
    OpenRouter — بوابة موحّدة.  model_id بصيغة: openrouter/provider/model-name[:free]
    """
    from backend.config import get_api_key
    api_key = get_api_key("openrouter")
    if not api_key:
        raise ValueError("مفتاح OpenRouter غير موجود")

    actual_model = model_id.removeprefix("openrouter/")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    response = await client.chat.completions.create(
        model=actual_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=1024,
        extra_headers={
            "HTTP-Referer": "https://affiliate-automation.app",
            "X-Title": "Affiliate Automation System",
        },
    )

    if not response.choices:
        raise RuntimeError("OpenRouter returned no choices")

    content = response.choices[0].message.content
    if not content:
        # بعض النماذج المجانية قد ترجع None عند الاستنفاد
        finish = getattr(response.choices[0], "finish_reason", "unknown")
        raise RuntimeError(f"OpenRouter returned empty content (finish_reason={finish})")

    return content


_PROVIDER_CALLERS = {
    "anthropic":  _call_anthropic,
    "openai":     _call_openai,
    "google":     _call_google,
    "groq":       _call_groq,
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


async def generate_content(product: dict, llm: str = "auto") -> tuple[str, str]:
    """
    يولّد محتوى عربي للمنتج.
    يُرجع: (النص المولَّد, اسم النموذج المستخدم)
    عند الفشل: يرجع القالب الثابت مع سبب الفشل في model_used للتشخيص.
    """
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
        try:
            logger.info(f"توليد المحتوى بـ {model_id}")
            content = await caller(model_id, user_prompt)
            if content and len(content.strip()) > 30:
                logger.info(f"نجح {model_id} ✓")
                return content, model_id
            last_error = RuntimeError(f"محتوى قصير/فارغ (طول={len(content) if content else 0})")
            logger.warning(f"{model_id}: {last_error}")
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
            result = await caller(mid, prompt, system=sys)
            if result and len(result.strip()) > 5:
                return result
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
        try:
            logger.info(f"توليد محتوى عرض اليوم بـ {model_id}")
            content = await caller(model_id, user_prompt, system=DEAL_OF_DAY_SYSTEM)
            if content and len(content.strip()) > 30:
                return content, model_id
            last_error = RuntimeError(f"محتوى قصير/فارغ")
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
        if used_model == "fallback-template":
            return {"success": False, "message": "فشل التوليد — تحقق من صلاحية المفتاح"}
        return {
            "success": True,
            "message": f"متصل ✓ — {used_model} جاهز",
            "preview": content[:150],
        }
    except Exception as exc:
        return {"success": False, "message": str(exc)}


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
