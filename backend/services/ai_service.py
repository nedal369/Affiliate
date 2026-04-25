"""
ai_service.py — توليد محتوى عربي جذاب باستخدام متعدد الـ LLM
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── النماذج المدعومة ────────────────────────────────────────────────────────────
SUPPORTED_MODELS = {
    # Anthropic
    "claude-opus-4-5": {
        "provider": "anthropic",
        "name": "Claude Opus 4.5",
        "speed": "slow",
        "quality": "highest",
        "label": "Claude Opus 4.5 — بطيء / جودة قصوى",
    },
    "claude-sonnet-4-5": {
        "provider": "anthropic",
        "name": "Claude Sonnet 4.5",
        "speed": "medium",
        "quality": "high",
        "label": "Claude Sonnet 4.5 — متوسط / جودة عالية",
    },
    "claude-haiku-4-5": {
        "provider": "anthropic",
        "name": "Claude Haiku 4.5",
        "speed": "fast",
        "quality": "good",
        "label": "Claude Haiku 4.5 — سريع / جودة جيدة",
    },
    # OpenAI
    "gpt-4o": {
        "provider": "openai",
        "name": "GPT-4o",
        "speed": "medium",
        "quality": "high",
        "label": "GPT-4o — متوسط / جودة عالية",
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "name": "GPT-4o Mini",
        "speed": "fast",
        "quality": "good",
        "label": "GPT-4o Mini — سريع / جودة جيدة",
    },
    "gpt-4-turbo": {
        "provider": "openai",
        "name": "GPT-4 Turbo",
        "speed": "slow",
        "quality": "highest",
        "label": "GPT-4 Turbo — بطيء / جودة قصوى",
    },
    # Google
    "gemini-1.5-pro": {
        "provider": "google",
        "name": "Gemini 1.5 Pro",
        "speed": "medium",
        "quality": "high",
        "label": "Gemini 1.5 Pro — متوسط / جودة عالية",
    },
    "gemini-1.5-flash": {
        "provider": "google",
        "name": "Gemini 1.5 Flash",
        "speed": "fast",
        "quality": "good",
        "label": "Gemini 1.5 Flash — سريع / جودة جيدة",
    },
    # Groq
    "llama-3.3-70b-versatile": {
        "provider": "groq",
        "name": "Llama 3.3 70B",
        "speed": "fastest",
        "quality": "good",
        "label": "Llama 3.3 70B (Groq) — الأسرع / جودة جيدة",
    },
    "mixtral-8x7b-32768": {
        "provider": "groq",
        "name": "Mixtral 8x7B",
        "speed": "fastest",
        "quality": "good",
        "label": "Mixtral 8x7B (Groq) — الأسرع / جودة جيدة",
    },
    # OpenRouter — بوابة موحّدة لمئات النماذج بمفتاح واحد
    "openrouter/meta-llama/llama-3.3-70b-instruct": {
        "provider": "openrouter",
        "name": "Llama 3.3 70B (OpenRouter)",
        "speed": "fast",
        "quality": "good",
        "label": "Llama 3.3 70B via OpenRouter — مجاني / جودة جيدة",
    },
    "openrouter/google/gemini-2.0-flash-exp": {
        "provider": "openrouter",
        "name": "Gemini 2.0 Flash (OpenRouter)",
        "speed": "fast",
        "quality": "high",
        "label": "Gemini 2.0 Flash via OpenRouter — سريع / جودة عالية",
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
التقييم: {rating}/5 ({rating_count} تقييم)
الرابط: {affiliate_url}

أضف في نهاية البوست: {affiliate_url}"""

FALLBACK_TEMPLATE = """🛒 عرض مميز لا تفوّته!

✨ {title}

💰 السعر الأصلي: {price_original} ريال
🔥 السعر الحالي: {price_current} ريال
📉 خصم {discount_pct}%

⭐ التقييم: {rating}/5 ({rating_count} تقييم)

🔗 اطلبه الآن: {affiliate_url}

#تسوق_أونلاين #عروض_أمازون #خصومات"""


# ── دوال الاستدعاء لكل مزوّد ────────────────────────────────────────────────────
async def _call_anthropic(model_id: str, user_prompt: str) -> str:
    from backend.config import get_api_key
    api_key = get_api_key("anthropic")
    if not api_key:
        raise ValueError("مفتاح Anthropic غير موجود")
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model=model_id,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


async def _call_openai(model_id: str, user_prompt: str) -> str:
    from backend.config import get_api_key
    api_key = get_api_key("openai")
    if not api_key:
        raise ValueError("مفتاح OpenAI غير موجود")
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1024,
    )
    return response.choices[0].message.content


_GEMINI_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
]

_GEMINI_FINISH_REASONS = {
    0: "UNSPECIFIED",
    1: "STOP",
    2: "MAX_TOKENS",
    3: "SAFETY",
    4: "RECITATION",
    5: "OTHER",
}


async def _call_gemini(model_id: str, system_prompt: str, user_prompt: str) -> str:
    """
    استدعاء Gemini مع معالجة شاملة لكل حالات الفشل.
    يلتقط: response.text المفقود، الحجب بـ safety filters، الردود الفارغة.
    """
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

    # فحص prompt feedback (هل تم حجب الـ prompt نفسه؟)
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

    if fr_value not in (1, 2):  # STOP أو MAX_TOKENS مقبولان
        raise RuntimeError(f"Gemini blocked content (finish_reason={fr_name})")

    # محاولة استخراج النص بأمان
    text = ""
    try:
        text = response.text or ""
    except Exception as e:
        # أحياناً response.text يرمي عند parts فارغة
        try:
            parts = getattr(candidate.content, "parts", []) or []
            text = "".join(getattr(p, "text", "") for p in parts)
        except Exception:
            raise RuntimeError(f"Gemini text extraction failed: {e}")

    if not text or len(text.strip()) < 10:
        raise RuntimeError(f"Gemini returned empty/short text (finish={fr_name})")

    return text


async def _call_google(model_id: str, user_prompt: str) -> str:
    return await _call_gemini(model_id, SYSTEM_PROMPT, user_prompt)


async def _call_groq(model_id: str, user_prompt: str) -> str:
    from backend.config import get_api_key
    api_key = get_api_key("groq")
    if not api_key:
        raise ValueError("مفتاح Groq غير موجود")
    from groq import AsyncGroq
    client = AsyncGroq(api_key=api_key)
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1024,
    )
    return response.choices[0].message.content


async def _call_openrouter(model_id: str, user_prompt: str) -> str:
    """
    OpenRouter — بوابة موحّدة لمئات النماذج بمفتاح واحد.
    يستخدم OpenAI-compatible API.
    model_id يكون بصيغة: openrouter/provider/model-name
    """
    from backend.config import get_api_key
    api_key = get_api_key("openrouter")
    if not api_key:
        raise ValueError("مفتاح OpenRouter غير موجود")

    # أزل بادئة "openrouter/" من اسم النموذج
    actual_model = model_id.removeprefix("openrouter/")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    response = await client.chat.completions.create(
        model=actual_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=1024,
        extra_headers={
            "HTTP-Referer": "https://affiliate-automation.app",
            "X-Title": "Affiliate Automation System",
        },
    )
    return response.choices[0].message.content


_PROVIDER_CALLERS = {
    "anthropic":  _call_anthropic,
    "openai":     _call_openai,
    "google":     _call_google,
    "groq":       _call_groq,
    "openrouter": _call_openrouter,
}


# ── الدالة الرئيسية ──────────────────────────────────────────────────────────────
async def generate_content(product: dict, llm: str = "auto") -> tuple[str, str]:
    """
    يولّد محتوى عربي للمنتج.
    يُرجع: (النص المولَّد, اسم النموذج المستخدم)
    """
    from backend.config import get_setting

    user_prompt = USER_PROMPT_TEMPLATE.format(
        title=product.get("title", ""),
        price_original=product.get("price_original", 0),
        price_current=product.get("price_current", 0),
        discount_pct=product.get("discount_pct", 0),
        rating=product.get("rating", 0),
        rating_count=product.get("rating_count", 0),
        affiliate_url=product.get("affiliate_url", ""),
    )

    if llm == "auto":
        # حدد قائمة الأولوية من الإعدادات
        priority = get_setting("llm_priority", ["claude-sonnet-4-5", "gpt-4o-mini", "llama-3.3-70b-versatile"])
        primary = get_setting("primary_llm", "claude-sonnet-4-5")
        # أضف النموذج الأساسي في المقدمة إذا لم يكن موجوداً
        if primary and primary not in priority:
            priority = [primary] + priority
        models_to_try = priority
    else:
        models_to_try = [llm]

    fallback_enabled = get_setting("llm_fallback", True)

    last_error = None
    for model_id in models_to_try:
        if model_id not in SUPPORTED_MODELS:
            logger.warning(f"نموذج غير معروف: {model_id}")
            continue

        provider = SUPPORTED_MODELS[model_id]["provider"]
        caller = _PROVIDER_CALLERS.get(provider)
        if not caller:
            continue

        try:
            logger.info(f"محاولة توليد المحتوى بـ {model_id}")
            content = await caller(model_id, user_prompt)
            if content and len(content) > 30:
                return content, model_id
            # تسجيل صريح بدلاً من التخطي الصامت
            last_error = RuntimeError(f"{model_id} returned short/empty content (len={len(content) if content else 0})")
            logger.warning(f"فشل {model_id}: محتوى قصير أو فارغ")
            if not fallback_enabled:
                break
        except Exception as exc:
            last_error = exc
            logger.warning(f"فشل {model_id}: {exc}")
            if not fallback_enabled:
                break

    # Fallback ثابت عند فشل كل النماذج
    logger.error(f"فشلت كل النماذج — استخدام template ثابت. آخر خطأ: {last_error}")
    content = FALLBACK_TEMPLATE.format(
        title=product.get("title", "منتج مميز"),
        price_original=product.get("price_original", 0),
        price_current=product.get("price_current", 0),
        discount_pct=product.get("discount_pct", 0),
        rating=product.get("rating", 0),
        rating_count=product.get("rating_count", 0),
        affiliate_url=product.get("affiliate_url", ""),
    )
    return content, "fallback-template"


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
        return {
            "success": True,
            "message": f"متصل ✓ — {used_model} جاهز",
            "preview": content[:100],
        }
    except Exception as exc:
        return {"success": False, "message": str(exc)}


async def get_available_models() -> list:
    return [{"id": k, **v} for k, v in SUPPORTED_MODELS.items()]


async def call_ai(prompt: str, system: str = "", model_id: str = "auto") -> str:
    """
    استدعاء AI بـ prompt مخصص (بدون قيود نموذج التسويق).
    يُستخدم لمهام مثل اختيار المنتجات وترتيبها.
    يُرجع: النص الخام من النموذج.
    """
    from backend.config import get_setting

    if model_id == "auto":
        priority = get_setting("llm_priority", ["gemini-1.5-flash", "gpt-4o-mini", "llama-3.3-70b-versatile"])
        primary = get_setting("primary_llm", "gemini-1.5-flash")
        if primary and primary not in priority:
            priority = [primary] + priority
        models_to_try = priority
    else:
        models_to_try = [model_id]

    for mid in models_to_try:
        if mid not in SUPPORTED_MODELS:
            continue
        provider = SUPPORTED_MODELS[mid]["provider"]
        try:
            if provider == "anthropic":
                from backend.config import get_api_key
                import anthropic
                api_key = get_api_key("anthropic")
                if not api_key:
                    continue
                client = anthropic.AsyncAnthropic(api_key=api_key)
                msg = await client.messages.create(
                    model=mid,
                    max_tokens=1024,
                    system=system or "أنت مساعد ذكي.",
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text
            elif provider == "openai":
                from backend.config import get_api_key
                from openai import AsyncOpenAI
                api_key = get_api_key("openai")
                if not api_key:
                    continue
                client = AsyncOpenAI(api_key=api_key)
                resp = await client.chat.completions.create(
                    model=mid,
                    messages=[
                        {"role": "system", "content": system or "أنت مساعد ذكي."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=512,
                )
                return resp.choices[0].message.content
            elif provider == "google":
                return await _call_gemini(mid, system or "أنت مساعد ذكي.", prompt)
            elif provider == "groq":
                from backend.config import get_api_key
                from groq import AsyncGroq
                api_key = get_api_key("groq")
                if not api_key:
                    continue
                client = AsyncGroq(api_key=api_key)
                resp = await client.chat.completions.create(
                    model=mid,
                    messages=[
                        {"role": "system", "content": system or "أنت مساعد ذكي."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=512,
                )
                return resp.choices[0].message.content
            elif provider == "openrouter":
                from backend.config import get_api_key
                from openai import AsyncOpenAI
                api_key = get_api_key("openrouter")
                if not api_key:
                    continue
                actual_model = mid.removeprefix("openrouter/")
                client = AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
                resp = await client.chat.completions.create(
                    model=actual_model,
                    messages=[
                        {"role": "system", "content": system or "أنت مساعد ذكي."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=512,
                )
                return resp.choices[0].message.content
        except Exception as exc:
            logger.warning(f"call_ai failed with {mid}: {exc}")
            continue

    raise RuntimeError("جميع نماذج AI غير متاحة")
