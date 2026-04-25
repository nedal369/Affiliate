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
    "openrouter/google/gemini-2.0-flash-exp:free": {
        "provider": "openrouter",
        "name": "Gemini 2.0 Flash (مجاني)",
        "speed": "fast",
        "quality": "high",
        "label": "Gemini 2.0 Flash via OpenRouter — مجاني / جودة عالية",
    },
    "openrouter/google/gemini-flash-1.5": {
        "provider": "openrouter",
        "name": "Gemini 1.5 Flash (OpenRouter)",
        "speed": "fast",
        "quality": "good",
        "label": "Gemini 1.5 Flash via OpenRouter — سريع / جودة جيدة",
    },
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": {
        "provider": "openrouter",
        "name": "Llama 3.3 70B (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Llama 3.3 70B via OpenRouter — مجاني / جودة جيدة",
    },
    "openrouter/meta-llama/llama-3.3-70b-instruct": {
        "provider": "openrouter",
        "name": "Llama 3.3 70B (OpenRouter)",
        "speed": "fast",
        "quality": "good",
        "label": "Llama 3.3 70B via OpenRouter — سريع / جودة جيدة",
    },
    "openrouter/qwen/qwen-2.5-72b-instruct:free": {
        "provider": "openrouter",
        "name": "Qwen 2.5 72B (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Qwen 2.5 72B via OpenRouter — مجاني / جودة جيدة",
    },
    "openrouter/mistralai/mistral-7b-instruct:free": {
        "provider": "openrouter",
        "name": "Mistral 7B (مجاني)",
        "speed": "fast",
        "quality": "good",
        "label": "Mistral 7B via OpenRouter — مجاني / جودة جيدة",
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


# ── اكتشاف المفاتيح المتاحة ───────────────────────────────────────────────────

def _build_model_priority(custom_system: bool = False) -> list[str]:
    """
    يبني قائمة النماذج الجاهزة بناءً على المفاتيح المضبوطة فعلاً.
    يُقدِّم OpenRouter إذا كان مفتاحه موجوداً، ثم الباقي.
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

    # ① النموذج الأساسي أولاً إذا كان مفتاحه موجوداً
    if primary and key_available(primary):
        ordered.append(primary)

    # ② قائمة الأولوية المحفوظة في الإعدادات
    for mid in (saved_list if isinstance(saved_list, list) else []):
        if mid not in ordered and key_available(mid):
            ordered.append(mid)

    # ③ إذا لم يُضبط شيء — جرّب كل نموذج مفتاحه موجود (OpenRouter أولاً)
    if not ordered:
        # نرتب: openrouter أولاً ثم groq ثم البقية
        provider_order = ["openrouter", "groq", "anthropic", "openai", "google"]
        for prov in provider_order:
            for mid, info in SUPPORTED_MODELS.items():
                if info["provider"] == prov and mid not in ordered and key_available(mid):
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

async def generate_content(product: dict, llm: str = "auto") -> tuple[str, str]:
    """
    يولّد محتوى عربي للمنتج.
    يُرجع: (النص المولَّد, اسم النموذج المستخدم)
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(
        title=product.get("title", ""),
        price_original=product.get("price_original", 0),
        price_current=product.get("price_current", 0),
        discount_pct=product.get("discount_pct", 0),
        rating=product.get("rating", 0),
        rating_count=product.get("rating_count", 0),
        affiliate_url=product.get("affiliate_url", ""),
    )

    models_to_try = _build_model_priority() if llm == "auto" else [llm]

    if not models_to_try:
        logger.warning("لا توجد نماذج AI مضبوطة — استخدام template ثابت")
        return _make_fallback(product), "fallback-template"

    last_error: Optional[Exception] = None
    for model_id in models_to_try:
        if model_id not in SUPPORTED_MODELS:
            logger.warning(f"نموذج غير معروف: {model_id}")
            continue

        provider = SUPPORTED_MODELS[model_id]["provider"]
        caller = _PROVIDER_CALLERS.get(provider)
        if not caller:
            continue

        try:
            logger.info(f"توليد المحتوى بـ {model_id}")
            content = await caller(model_id, user_prompt)
            if content and len(content.strip()) > 30:
                logger.info(f"نجح {model_id} ✓")
                return content, model_id
            last_error = RuntimeError(f"{model_id}: محتوى قصير أو فارغ (طول={len(content) if content else 0})")
            logger.warning(str(last_error))
        except Exception as exc:
            last_error = exc
            logger.warning(f"فشل {model_id}: {exc}")

    logger.error(f"فشلت كل النماذج ({len(models_to_try)} محاولة) — آخر خطأ: {last_error}")
    return _make_fallback(product), "fallback-template"


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
        rating=product.get("rating", 0),
        rating_count=product.get("rating_count", 0),
        affiliate_url=product.get("affiliate_url", ""),
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
