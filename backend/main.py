"""
main.py — FastAPI Application — نقطة الدخول الرئيسية
"""
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Body, Query, Request, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


# ── Startup / Shutdown ────────────────────────────────────────────────────────────
async def _ensure_default_admin():
    """إنشاء حساب admin في الخلفية لتجنب blocking الـ event loop أثناء الـ startup"""
    try:
        from backend.config import create_default_admin
        await asyncio.to_thread(create_default_admin)
    except Exception as e:
        logger.error(f"خطأ في إنشاء حساب Admin: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from backend.config import init_db
    from backend.scheduler import setup_scheduler
    init_db()          # سريع: إنشاء الجداول والإعدادات فقط، بدون bcrypt
    setup_scheduler()
    logger.info("✅ النظام جاهز")
    asyncio.create_task(_ensure_default_admin())  # bcrypt في الخلفية بعد bind المنفذ
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


# ── Auth Middleware ───────────────────────────────────────────────────────────────
# المسارات العامة التي لا تحتاج تسجيل دخول
_PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/health"}
_PUBLIC_PREFIXES = ("/static/",)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # السماح بالمسارات العامة دون مصادقة
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    # قراءة الـ token من الـ cookie
    token = request.cookies.get("access_token")
    if not token:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "غير مصادق عليه"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)

    from backend.services.auth_service import get_user_from_token
    user = get_user_from_token(token)
    if not user:
        if path.startswith("/api/"):
            resp = JSONResponse({"detail": "جلسة منتهية — سجّل دخولك مجدداً"}, status_code=401)
        else:
            resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie("access_token")
        return resp

    # أضف بيانات المستخدم للـ request state
    request.state.user = user
    return await call_next(request)


# ── Helper: Current User ──────────────────────────────────────────────────────────
def _get_user(request: Request) -> dict:
    return getattr(request.state, "user", {})

def _require_admin(request: Request) -> dict:
    user = _get_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "صلاحيات المدير مطلوبة")
    return user


# ── صفحة تسجيل الدخول (مسار عام) ─────────────────────────────────────────────
@app.get("/login")
async def login_page():
    login_html = FRONTEND_DIR / "login.html"
    if login_html.exists():
        return FileResponse(str(login_html))
    return JSONResponse({"error": "login.html غير موجود"}, status_code=404)


# ── الصفحة الرئيسية (محمية) ───────────────────────────────────────────────────
@app.get("/")
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Affiliate Automation System", "status": "running"}


# ═══════════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════════

class LoginBody(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
async def login(body: LoginBody, response: Response):
    from backend.services.auth_service import authenticate_user, create_access_token
    user = authenticate_user(body.username.strip(), body.password)
    if not user:
        raise HTTPException(401, "اسم المستخدم أو كلمة المرور غير صحيحة")

    token = create_access_token(user["id"], user["username"], user["role"])
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,   # 7 أيام
        secure=False,                 # True في الإنتاج مع HTTPS
    )
    return {"success": True, "user": user}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"success": True}


@app.get("/api/auth/me")
async def get_me(request: Request):
    return _get_user(request)


# ═══════════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT ENDPOINTS (Admin only)
# ═══════════════════════════════════════════════════════════════════════════════════

@app.get("/api/users")
async def list_users(request: Request):
    _require_admin(request)
    from backend.config import SessionLocal
    from backend.models import User
    with SessionLocal() as db:
        users = db.query(User).order_by(User.created_at).all()
        return [_user_to_dict(u) for u in users]


class CreateUserBody(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    role: str = "user"

@app.post("/api/users")
async def create_user(request: Request, body: CreateUserBody):
    _require_admin(request)
    from backend.config import SessionLocal
    from backend.models import User
    from backend.services.auth_service import get_password_hash

    if len(body.password) < 6:
        raise HTTPException(400, "كلمة المرور يجب أن تكون 6 أحرف على الأقل")
    if body.role not in ("admin", "user"):
        raise HTTPException(400, "الدور يجب أن يكون admin أو user")

    with SessionLocal() as db:
        exists = db.query(User).filter(User.username == body.username.strip()).first()
        if exists:
            raise HTTPException(400, "اسم المستخدم موجود بالفعل")
        user = User(
            username=body.username.strip(),
            password_hash=get_password_hash(body.password),
            display_name=body.display_name or body.username,
            role=body.role,
            active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"success": True, "user": _user_to_dict(user)}


class UpdateUserBody(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    new_password: Optional[str] = None

@app.patch("/api/users/{user_id}")
async def update_user(request: Request, user_id: int, body: UpdateUserBody):
    _require_admin(request)
    from backend.config import SessionLocal
    from backend.models import User
    from backend.services.auth_service import get_password_hash

    current = _get_user(request)
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(404, "المستخدم غير موجود")

        # منع حذف أو تعطيل الـ admin الأخير
        if user.role == "admin" and body.active is False:
            admin_count = db.query(User).filter(User.role == "admin", User.active == True).count()
            if admin_count <= 1:
                raise HTTPException(400, "لا يمكن تعطيل آخر حساب مدير")

        if body.display_name is not None:
            user.display_name = body.display_name
        if body.role is not None:
            if body.role not in ("admin", "user"):
                raise HTTPException(400, "الدور غير صالح")
            user.role = body.role
        if body.active is not None:
            user.active = body.active
        if body.new_password:
            if len(body.new_password) < 6:
                raise HTTPException(400, "كلمة المرور يجب أن تكون 6 أحرف على الأقل")
            user.password_hash = get_password_hash(body.new_password)

        db.commit()
        db.refresh(user)
        return {"success": True, "user": _user_to_dict(user)}


@app.delete("/api/users/{user_id}")
async def delete_user(request: Request, user_id: int):
    _require_admin(request)
    current = _get_user(request)
    if current.get("id") == user_id:
        raise HTTPException(400, "لا يمكنك حذف حسابك الخاص")
    from backend.config import SessionLocal
    from backend.models import User
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(404, "المستخدم غير موجود")
        if user.role == "admin":
            admin_count = db.query(User).filter(User.role == "admin", User.active == True).count()
            if admin_count <= 1:
                raise HTTPException(400, "لا يمكن حذف آخر حساب مدير")
        db.delete(user)
        db.commit()
    return {"success": True}


# تغيير كلمة المرور الخاصة بالمستخدم الحالي
class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/auth/change-password")
async def change_password(request: Request, body: ChangePasswordBody):
    current = _get_user(request)
    from backend.config import SessionLocal
    from backend.models import User
    from backend.services.auth_service import verify_password, get_password_hash

    if len(body.new_password) < 6:
        raise HTTPException(400, "كلمة المرور الجديدة يجب أن تكون 6 أحرف على الأقل")

    with SessionLocal() as db:
        user = db.get(User, current["id"])
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(400, "كلمة المرور الحالية غير صحيحة")
        user.password_hash = get_password_hash(body.new_password)
        db.commit()
    return {"success": True}


def _user_to_dict(u) -> dict:
    return {
        "id":           u.id,
        "username":     u.username,
        "display_name": u.display_name or u.username,
        "role":         u.role,
        "active":       u.active,
        "created_at":   u.created_at.isoformat() if u.created_at else None,
        "last_login":   u.last_login.isoformat() if u.last_login else None,
    }


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
    "openrouter",
    "amazon_access_key", "amazon_secret_key", "amazon_associate_tag",
    "rapidapi_key",
    "apify",
    "telegram_token", "telegram_channel_id",
    "ultramsg_instance", "ultramsg_token", "whatsapp_number",
    "x_api_key", "x_api_secret", "x_access_token", "x_access_secret",
]


@app.get("/api/keys")
async def get_keys():
    from backend.config import get_masked_key, get_api_key, is_key_from_env, ENV_VAR_MAP
    result = {}
    for service in KNOWN_SERVICES:
        raw = get_api_key(service)
        from_env = is_key_from_env(service)
        result[service] = {
            "masked":    get_masked_key(service),
            "has_key":   bool(raw),
            "from_env":  from_env,
            "env_var":   ENV_VAR_MAP.get(service, ""),
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

    elif service == "rapidapi_key":
        from backend.services.rapidapi_amazon import test_rapidapi_connection
        return await test_rapidapi_connection()

    elif service == "openrouter":
        if not api_key:
            return {"success": False, "message": "المفتاح غير موجود"}
        try:
            from backend.services.ai_service import test_model
            result = await test_model("openrouter/meta-llama/llama-3.3-70b-instruct")
            return result
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


# ── إضافة رابط إحالة يدوي (جلب تلقائي للبيانات) ────────────────────────────────
class ManualLinkBody(BaseModel):
    url: str
    auto_publish: bool = False


@app.post("/api/manual-link")
async def add_manual_link(body: ManualLinkBody):
    from backend.config import SessionLocal
    from backend.models import Product
    from backend.services.ai_service import generate_content
    from backend.services.link_fetcher import fetch_product_data, resolve_affiliate_url

    url = body.url.strip()
    if not url:
        raise HTTPException(400, "الرابط مطلوب")

    # ① بناء رابط الإحالة (يُضيف tag= تلقائياً إذا لم يكن موجوداً)
    affiliate_url = resolve_affiliate_url(url)

    # ② جلب بيانات المنتج من الصفحة
    fetched = await fetch_product_data(url)

    title = fetched.get("title") or "منتج من رابط إحالة"

    product_data = {
        "title":          title,
        "price_original": fetched.get("price_original") or 0.0,
        "price_current":  fetched.get("price_current")  or 0.0,
        "discount_pct":   fetched.get("discount_pct")   or 0,
        "rating":         fetched.get("rating")          or 0.0,
        "rating_count":   fetched.get("rating_count")    or 0,
        "affiliate_url":  affiliate_url,
    }

    # ③ توليد المحتوى
    ai_content, model_used = await generate_content(product_data)

    # ④ حفظ في قاعدة البيانات
    with SessionLocal() as db:
        product = Product(
            title=title,
            title_ar=title,
            source="manual",
            amazon_asin=fetched.get("amazon_asin"),
            affiliate_url=affiliate_url,
            price_current=product_data["price_current"],
            price_original=product_data["price_original"],
            discount_pct=product_data["discount_pct"],
            rating=product_data["rating"],
            rating_count=product_data["rating_count"],
            image_url=fetched.get("image_url"),
            ai_content=ai_content,
            ai_model_used=model_used,
            status="pending",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        product_id = product.id
        result = _product_to_dict(product)

    result["fetch_source"] = fetched.get("fetch_source", "scrape")
    result["affiliate_url"] = affiliate_url   # الرابط مع tag=

    # ⑤ نشر تلقائي إذا طُلب
    if body.auto_publish:
        from backend.services.publisher import publish_product
        pub_result = await publish_product(product_id)
        result["auto_published"] = True
        result["publish_result"] = pub_result
    else:
        result["auto_published"] = False

    return {"success": True, "product": result}


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


# ═══════════════════════════════════════════════════════════════════════════════════
# DEALS FINDER ENDPOINTS — اكتشاف العروض بالذكاء الاصطناعي
# ═══════════════════════════════════════════════════════════════════════════════════

@app.get("/api/deals/categories")
async def get_deals_categories():
    """قائمة التصنيفات المتاحة للبحث"""
    from backend.services.deals_finder_service import get_all_categories
    return get_all_categories()


class DiscoverDealsBody(BaseModel):
    category_ids: list[str]


@app.post("/api/deals/discover")
async def discover_deals(body: DiscoverDealsBody, request: Request):
    """اكتشاف أفضل العروض للتصنيفات المحددة باستخدام RapidAPI + AI"""
    if not body.category_ids:
        raise HTTPException(400, "يجب اختيار تصنيف واحد على الأقل")
    if len(body.category_ids) > 8:
        raise HTTPException(400, "الحد الأقصى 8 تصنيفات في البحث الواحد")
    try:
        from backend.services.deals_finder_service import discover_deals as _discover
        results = await _discover(body.category_ids)
        return {"success": True, "results": results}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error(f"خطأ في اكتشاف العروض: {exc}")
        raise HTTPException(500, f"خطأ في الاكتشاف: {exc}")


class GenerateDealsContentBody(BaseModel):
    deals: list[dict]


@app.post("/api/deals/generate-content")
async def generate_deals_content(body: GenerateDealsContentBody, request: Request):
    """توليد محتوى عربي لقائمة منتجات محددة"""
    if not body.deals:
        raise HTTPException(400, "لا توجد منتجات محددة")
    if len(body.deals) > 20:
        raise HTTPException(400, "الحد الأقصى 20 منتج في الطلب الواحد")

    from backend.services.ai_service import generate_content

    async def gen_one(deal: dict) -> dict:
        content, model_used = await generate_content(deal)
        return {
            "asin": deal.get("asin", ""),
            "content": content,
            "model_used": model_used,
        }

    results = await asyncio.gather(*[gen_one(d) for d in body.deals], return_exceptions=True)
    output = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"خطأ في توليد المحتوى: {r}")
            output.append({"asin": "", "content": "", "error": str(r)})
        else:
            output.append(r)

    return {"success": True, "results": output}


class PublishDealsBody(BaseModel):
    deals: list[dict]


@app.post("/api/deals/publish")
async def publish_deals(body: PublishDealsBody, request: Request):
    """حفظ ونشر العروض المحددة مع محتواها"""
    if not body.deals:
        raise HTTPException(400, "لا توجد منتجات للنشر")

    from backend.config import SessionLocal
    from backend.models import Product
    from backend.services.publisher import publish_product

    published_count = 0
    results = []

    for deal in body.deals:
        try:
            with SessionLocal() as db:
                product = Product(
                    title=deal.get("title", ""),
                    source="deals_finder",
                    amazon_asin=deal.get("asin", ""),
                    amazon_url=deal.get("amazon_url", ""),
                    affiliate_url=deal.get("affiliate_url", ""),
                    price_original=deal.get("price_original", 0),
                    price_current=deal.get("price_current", 0),
                    discount_pct=deal.get("discount_pct", 0),
                    rating=deal.get("rating", 0),
                    rating_count=deal.get("rating_count", 0),
                    image_url=deal.get("image_url", ""),
                    ai_content=deal.get("content", ""),
                    ai_model_used=deal.get("model_used", ""),
                    status="approved",
                )
                db.add(product)
                db.commit()
                db.refresh(product)
                product_id = product.id

            pub_result = await publish_product(product_id)
            published_count += 1
            results.append({"asin": deal.get("asin", ""), "success": True, "product_id": product_id, **pub_result})
        except Exception as exc:
            logger.error(f"خطأ في نشر المنتج {deal.get('asin', '')}: {exc}")
            results.append({"asin": deal.get("asin", ""), "success": False, "error": str(exc)})

    return {"success": True, "published": published_count, "total": len(body.deals), "results": results}


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
