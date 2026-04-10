"""
main.py — FastAPI Application — نقطة الدخول الرئيسية
"""
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


# ── Startup / Shutdown ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    from backend.config import init_db
    from backend.scheduler import setup_scheduler
    init_db()
    setup_scheduler()
    logger.info("✅ النظام جاهز")
    yield
    from backend.scheduler import stop_scheduler
    stop_scheduler()


app = FastAPI(title="Affiliate Automation System", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files ──────────────────────────────────────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Affiliate Automation System", "status": "running"}


# ═══════════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════════

# ── إحصاءات ──────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats():
    from backend.services.tracker import get_today_stats
    from backend.config import get_setting
    stats = await get_today_stats()
    stats["system_enabled"] = get_setting("system_enabled", True)
    return stats


# ── قائمة الانتظار ────────────────────────────────────────────────────────────────
@app.get("/api/queue")
async def get_queue():
    from backend.config import SessionLocal
    from backend.models import Product
    with SessionLocal() as db:
        products = (
            db.query(Product)
            .filter(Product.status == "pending")
            .order_by(Product.created_at.desc())
            .all()
        )
        return [_product_to_dict(p) for p in products]


@app.post("/api/queue/{product_id}/approve")
async def approve_product(product_id: int):
    from backend.config import SessionLocal
    from backend.models import Product
    from backend.services.publisher import publish_product

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        product.status = "approved"
        db.commit()

    result = await publish_product(product_id)
    return {"success": True, "publish_result": result}


@app.post("/api/queue/{product_id}/reject")
async def reject_product(product_id: int):
    from backend.config import SessionLocal
    from backend.models import Product
    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        product.status = "rejected"
        db.commit()
    return {"success": True}


class EditProductBody(BaseModel):
    ai_content: Optional[str] = None
    title_ar: Optional[str] = None


@app.put("/api/queue/{product_id}/edit")
async def edit_product(product_id: int, body: EditProductBody):
    from backend.config import SessionLocal
    from backend.models import Product
    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        if body.ai_content is not None:
            product.ai_content = body.ai_content
        if body.title_ar is not None:
            product.title_ar = body.title_ar
        db.commit()
    return {"success": True}


@app.post("/api/queue/approve-all")
async def approve_all():
    from backend.config import SessionLocal
    from backend.models import Product
    from backend.services.publisher import publish_product

    with SessionLocal() as db:
        pending = db.query(Product).filter(Product.status == "pending").all()
        ids = [p.id for p in pending]
        for p in pending:
            p.status = "approved"
        db.commit()

    results = await asyncio.gather(*[publish_product(pid) for pid in ids], return_exceptions=True)
    return {"success": True, "published": len(ids)}


# ── سجل النشر ──────────────────────────────────────────────────────────────────
@app.get("/api/history")
async def get_history(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    channel: Optional[str] = None,
    status: Optional[str] = None,
):
    from backend.config import SessionLocal
    from backend.models import Product, PublishLog
    import sqlalchemy as sa

    with SessionLocal() as db:
        q = db.query(Product).filter(Product.status.in_(["published", "failed", "rejected"]))
        if status:
            q = q.filter(Product.status == status)
        total = q.count()
        products = q.order_by(Product.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

        result = []
        for p in products:
            logs = db.query(PublishLog).filter(PublishLog.product_id == p.id).all()
            channels_info = {l.channel: {"success": l.success, "clicks": l.clicks} for l in logs}
            total_clicks = sum(l.clicks for l in logs)
            d = _product_to_dict(p)
            d["channels_info"] = channels_info
            d["total_clicks"] = total_clicks
            result.append(d)

    return {"total": total, "page": page, "per_page": per_page, "items": result}


# ── الإعدادات ──────────────────────────────────────────────────────────────────
@app.get("/api/settings")
async def get_settings_api():
    from backend.config import get_all_settings
    return get_all_settings()


@app.post("/api/settings")
async def save_settings(body: dict = Body(...)):
    from backend.config import set_setting
    from backend.scheduler import refresh_schedule
    for key, value in body.items():
        set_setting(key, value)
    refresh_schedule()
    return {"success": True}


# ── مفاتيح API ──────────────────────────────────────────────────────────────────
KNOWN_SERVICES = [
    "anthropic", "openai", "google", "groq",
    "amazon_access_key", "amazon_secret_key", "amazon_associate_tag",
    "apify",
    "telegram_token", "telegram_channel_id",
    "ultramsg_instance", "ultramsg_token", "whatsapp_number",
    "x_api_key", "x_api_secret", "x_access_token", "x_access_secret",
]


@app.get("/api/keys")
async def get_keys():
    from backend.config import get_masked_key, get_api_key
    result = {}
    for service in KNOWN_SERVICES:
        raw = get_api_key(service)
        result[service] = {
            "masked": get_masked_key(service),
            "has_key": bool(raw),
        }
    return result


class KeySaveBody(BaseModel):
    service: str
    key: str


@app.post("/api/keys")
async def save_key(body: KeySaveBody):
    from backend.config import set_api_key
    if not body.service or not body.key:
        raise HTTPException(400, "service و key مطلوبان")
    set_api_key(body.service, body.key.strip())
    return {"success": True}


# ── اختبار الاتصال ────────────────────────────────────────────────────────────────
@app.post("/api/keys/test/{service}")
async def test_key(service: str):
    from backend.config import get_api_key

    api_key = get_api_key(service)

    if service == "anthropic":
        if not api_key:
            return {"success": False, "message": "المفتاح غير موجود"}
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
            msg = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
            return {"success": True, "message": "متصل ✓ — Claude جاهز"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    elif service == "openai":
        if not api_key:
            return {"success": False, "message": "المفتاح غير موجود"}
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key)
            await client.models.list()
            return {"success": True, "message": "متصل ✓ — OpenAI جاهز"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    elif service == "google":
        if not api_key:
            return {"success": False, "message": "المفتاح غير موجود"}
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            models = await asyncio.to_thread(genai.list_models)
            return {"success": True, "message": "متصل ✓ — Gemini جاهز"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    elif service == "groq":
        if not api_key:
            return {"success": False, "message": "المفتاح غير موجود"}
        try:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=api_key)
            await client.models.list()
            return {"success": True, "message": "متصل ✓ — Groq جاهز"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    elif service == "amazon_access_key":
        from backend.services.amazon_service import test_connection
        return await test_connection()

    elif service == "apify":
        if not api_key:
            return {"success": False, "message": "المفتاح غير موجود"}
        try:
            from apify_client import ApifyClient
            client = ApifyClient(api_key)
            user = await asyncio.to_thread(client.user().get)
            return {"success": True, "message": f"متصل ✓ — {user.get('username', 'Apify')}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    elif service == "telegram_token":
        from backend.services.publisher import test_telegram
        return await test_telegram()

    elif service == "ultramsg_instance":
        from backend.services.publisher import test_whatsapp
        return await test_whatsapp()

    elif service == "x_api_key":
        from backend.services.publisher import test_x_connection
        return await test_x_connection()

    return {"success": False, "message": f"اختبار {service} غير مدعوم"}


# ── تشغيل فوري ────────────────────────────────────────────────────────────────────
@app.post("/api/run-now")
async def run_now():
    from backend.scheduler import fetch_and_queue
    asyncio.create_task(fetch_and_queue())
    return {"success": True, "message": "بدأت دورة الجلب في الخلفية"}


# ── إيقاف/تشغيل النظام ─────────────────────────────────────────────────────────
@app.post("/api/system/toggle")
async def toggle_system():
    from backend.config import get_setting, set_setting
    current = get_setting("system_enabled", True)
    new_state = not current
    set_setting("system_enabled", new_state)
    return {"success": True, "system_enabled": new_state}


# ── حالة القنوات ──────────────────────────────────────────────────────────────────
@app.get("/api/channels/status")
async def channels_status():
    from backend.config import get_api_key, get_setting
    return {
        "telegram": {
            "enabled": get_setting("enable_telegram", True),
            "connected": bool(get_api_key("telegram_token")),
        },
        "whatsapp": {
            "enabled": get_setting("enable_whatsapp", False),
            "connected": bool(get_api_key("ultramsg_instance")),
        },
        "x": {
            "enabled": get_setting("enable_x", False),
            "connected": bool(get_api_key("x_api_key")),
        },
    }


# ── التحليلات ──────────────────────────────────────────────────────────────────
@app.get("/api/analytics")
async def get_analytics(days: int = Query(30, ge=1, le=365)):
    from backend.services.tracker import get_analytics_data
    return await get_analytics_data(days)


# ── نماذج الذكاء الاصطناعي ────────────────────────────────────────────────────────
@app.get("/api/ai/models")
async def get_ai_models():
    from backend.services.ai_service import get_available_models
    return await get_available_models()


# ── الحسابات المراقبة ─────────────────────────────────────────────────────────────
@app.get("/api/accounts")
async def get_accounts():
    from backend.config import SessionLocal
    from backend.models import WatchedAccount
    with SessionLocal() as db:
        accounts = db.query(WatchedAccount).all()
        return [
            {
                "id": a.id,
                "platform": a.platform,
                "username": a.username,
                "active": a.active,
                "last_checked": a.last_checked.isoformat() if a.last_checked else None,
                "posts_found": a.posts_found,
            }
            for a in accounts
        ]


class AccountBody(BaseModel):
    platform: str
    username: str
    active: bool = True


@app.post("/api/accounts")
async def add_account(body: AccountBody):
    from backend.config import SessionLocal
    from backend.models import WatchedAccount
    with SessionLocal() as db:
        acc = WatchedAccount(platform=body.platform, username=body.username.lstrip("@"), active=body.active)
        db.add(acc)
        db.commit()
        db.refresh(acc)
        return {"success": True, "id": acc.id}


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int):
    from backend.config import SessionLocal
    from backend.models import WatchedAccount
    with SessionLocal() as db:
        acc = db.get(WatchedAccount, account_id)
        if not acc:
            raise HTTPException(404, "الحساب غير موجود")
        db.delete(acc)
        db.commit()
    return {"success": True}


@app.patch("/api/accounts/{account_id}")
async def toggle_account(account_id: int, body: dict = Body(...)):
    from backend.config import SessionLocal
    from backend.models import WatchedAccount
    with SessionLocal() as db:
        acc = db.get(WatchedAccount, account_id)
        if not acc:
            raise HTTPException(404, "الحساب غير موجود")
        if "active" in body:
            acc.active = body["active"]
        db.commit()
    return {"success": True}


# ── الكلمات المفتاحية ─────────────────────────────────────────────────────────────
@app.get("/api/keywords")
async def get_keywords():
    from backend.config import SessionLocal
    from backend.models import TrendKeyword
    with SessionLocal() as db:
        kws = db.query(TrendKeyword).all()
        return [
            {"id": k.id, "keyword": k.keyword, "platform": k.platform, "active": k.active}
            for k in kws
        ]


class KeywordBody(BaseModel):
    keyword: str
    platform: Optional[str] = None
    active: bool = True


@app.post("/api/keywords")
async def add_keyword(body: KeywordBody):
    from backend.config import SessionLocal
    from backend.models import TrendKeyword
    with SessionLocal() as db:
        kw = TrendKeyword(keyword=body.keyword, platform=body.platform, active=body.active)
        db.add(kw)
        db.commit()
        db.refresh(kw)
        return {"success": True, "id": kw.id}


@app.delete("/api/keywords/{keyword_id}")
async def delete_keyword(keyword_id: int):
    from backend.config import SessionLocal
    from backend.models import TrendKeyword
    with SessionLocal() as db:
        kw = db.get(TrendKeyword, keyword_id)
        if not kw:
            raise HTTPException(404, "الكلمة غير موجودة")
        db.delete(kw)
        db.commit()
    return {"success": True}


# ── Health Check ──────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── مساعدات ──────────────────────────────────────────────────────────────────────
def _product_to_dict(p) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "title_ar": p.title_ar,
        "source": p.source,
        "source_account": p.source_account,
        "source_views": p.source_views,
        "amazon_asin": p.amazon_asin,
        "amazon_url": p.amazon_url,
        "affiliate_url": p.affiliate_url,
        "price_original": p.price_original,
        "price_current": p.price_current,
        "discount_pct": p.discount_pct,
        "rating": p.rating,
        "rating_count": p.rating_count,
        "image_url": p.image_url,
        "ai_content": p.ai_content,
        "ai_model_used": p.ai_model_used,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
    }


# ── Entry point ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
