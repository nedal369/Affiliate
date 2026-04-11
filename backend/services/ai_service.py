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


async def _call_google(model_id: str, user_prompt: str) -> str:
    from backend.config import get_api_key
    api_key = get_api_key("google")
    if not api_key:
        raise ValueError("مفتاح Google Gemini غير موجود")
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_id, system_instruction=SYSTEM_PROMPT)
    response = await asyncio.to_thread(model.generate_content, user_prompt)
    return response.text


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
