"""
main.py — FastAPI Application — نقطة الدخول الرئيسية
"""
import logging
import asyncio
import os
import time
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

# ── إعدادات بيئة التشغيل ────────────────────────────────────────────────────────
IS_PRODUCTION = os.getenv("ENVIRONMENT", "").lower() == "production"
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",") if o.strip()
]


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
    logger.info(f"✅ النظام جاهز — البيئة: {'production' if IS_PRODUCTION else 'development'}")
    asyncio.create_task(_ensure_default_admin())  # bcrypt في الخلفية بعد bind المنفذ
    yield
    from backend.scheduler import stop_scheduler
    stop_scheduler()


app = FastAPI(title="Affiliate Automation System", lifespan=lifespan)

# ── Rate Limiting (slowapi) ───────────────────────────────────────────────────
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    limiter = Limiter(key_func=get_remote_address, default_limits=["1000/hour"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    _RATE_LIMITER_AVAILABLE = True
except ImportError:
    logger.warning("slowapi غير مثبّت — تخطّي rate limiting")
    _RATE_LIMITER_AVAILABLE = False
    limiter = None

# ── CORS — مقيّد على origins محددة ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ── Security Headers Middleware ─────────────────────────────────────────────
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── Static files ──────────────────────────────────────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Auth Middleware ───────────────────────────────────────────────────────────────
# المسارات العامة التي لا تحتاج تسجيل دخول
_PUBLIC_PATHS = {
    "/login",
    "/api/auth/login",
    "/api/health",
}
_PUBLIC_PREFIXES = ("/static/",)

# Token cache — يقلل DB queries في كل طلب
_token_cache: dict[str, tuple[dict, float]] = {}
_TOKEN_CACHE_TTL = 300  # 5 دقائق

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

    # ① تحقق من الـ cache أولاً (يوفّر DB query)
    now = time.time()
    cached = _token_cache.get(token)
    if cached and now < cached[1]:
        request.state.user = cached[0]
        return await call_next(request)

    # ② تحقق فعلي من DB
    from backend.services.auth_service import get_user_from_token
    user = get_user_from_token(token)
    if not user:
        _token_cache.pop(token, None)
        if path.startswith("/api/"):
            resp = JSONResponse({"detail": "جلسة منتهية — سجّل دخولك مجدداً"}, status_code=401)
        else:
            resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie("access_token")
        return resp

    # ③ خزّن في الـ cache + تنظيف دوري
    _token_cache[token] = (user, now + _TOKEN_CACHE_TTL)
    if len(_token_cache) > 500:
        # حذف المنتهي صلاحيته
        expired = [t for t, (_, exp) in _token_cache.items() if exp < now]
        for t in expired:
            _token_cache.pop(t, None)

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

def _client_ip(request: Request) -> str:
    """استخراج IP المستخدم — يحترم X-Forwarded-For خلف proxy"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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

def _login_rate_limit(func):
    """Decorator يطبّق rate limit إذا كان slowapi متوفر، وإلا يمرّر الدالة كما هي"""
    if _RATE_LIMITER_AVAILABLE:
        return limiter.limit("5/minute")(func)
    return func

@app.post("/api/auth/login")
@_login_rate_limit
async def login(request: Request, body: LoginBody, response: Response):
    from backend.services.auth_service import authenticate_user, create_access_token
    user = authenticate_user(body.username.strip(), body.password)
    if not user:
        raise HTTPException(401, "اسم المستخدم أو كلمة المرور غير صحيحة")

    token = create_access_token(user["id"], user["username"], user["role"])
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="strict" if IS_PRODUCTION else "lax",
        max_age=60 * 60 * 24 * 7,   # 7 أيام
        secure=IS_PRODUCTION,        # True فقط في الإنتاج (HTTPS)
    )
    return {"success": True, "user": user}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    # إبطال الـ token من الـ cache
    token = request.cookies.get("access_token")
    if token:
        _token_cache.pop(token, None)
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
    from backend.services.telegram_stats import get_subscriber_count
    stats = await get_today_stats()
    stats["system_enabled"] = get_setting("system_enabled", True)
    stats["autopilot_enabled"] = get_setting("autopilot_enabled", False)
    # عدد مشتركي قناة تلقرام (cached 5 دقائق داخلياً)
    try:
        tg = await get_subscriber_count()
        stats["telegram_subscribers"] = tg.get("count", 0)
        stats["telegram_subscribers_ok"] = tg.get("ok", False)
    except Exception:
        stats["telegram_subscribers"] = 0
        stats["telegram_subscribers_ok"] = False
    return stats


@app.get("/api/telegram/stats")
async def telegram_stats():
    """تفاصيل قناة تلقرام: عدد المشتركين + معلومات القناة."""
    from backend.services.telegram_stats import get_subscriber_count, get_chat_info
    subs = await get_subscriber_count()
    info = await get_chat_info()
    return {
        "subscribers": subs,
        "chat": info,
    }


# ── قائمة الانتظار ────────────────────────────────────────────────────────────────
@app.get("/api/queue")
async def get_queue(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """قائمة المنتجات في الانتظار مع pagination.
    للحفاظ على التوافق مع الواجهة القديمة، إذا لم تُمرَّر page نُرجع كل المنتجات (max 200).
    """
    from backend.config import SessionLocal
    from backend.models import Product
    with SessionLocal() as db:
        q = db.query(Product).filter(Product.status == "pending")
        total = q.count()
        items = (
            q.order_by(Product.created_at.desc())
             .offset((page - 1) * per_page)
             .limit(per_page)
             .all()
        )
        return {
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "items":    [_product_to_dict(p) for p in items],
        }


@app.post("/api/queue/{product_id}/approve")
async def approve_product(product_id: int):
    """قبول المنتج للجدولة — لا يُنشر فوراً بل ينتظر الموعد المجدول"""
    from backend.config import SessionLocal
    from backend.models import Product

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        product.status = "approved"
        db.commit()

    return {"success": True, "message": "تمت الموافقة — سيُنشر في الموعد المجدول"}


@app.post("/api/queue/{product_id}/publish-now")
async def publish_product_now(product_id: int):
    """نشر فوري استثنائي بخلاف الجدولة"""
    from backend.config import SessionLocal
    from backend.models import Product
    from backend.services.publisher import publish_product

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        if product.status not in ("pending", "approved"):
            raise HTTPException(400, "المنتج ليس في حالة انتظار")
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
    """قبول جميع المنتجات للجدولة — لا نشر فوري"""
    from backend.config import SessionLocal
    from backend.models import Product

    with SessionLocal() as db:
        pending = db.query(Product).filter(Product.status == "pending").all()
        count = len(pending)
        for p in pending:
            p.status = "approved"
        db.commit()

    return {"success": True, "approved": count, "message": f"تمت الموافقة على {count} منتج — ستُنشر حسب الجدولة"}


@app.get("/api/queue/schedule-info")
async def queue_schedule_info():
    """معلومات الجدولة القادمة وعدد المنتجات في كل حالة"""
    from backend.config import SessionLocal, get_setting
    from backend.models import Product
    from datetime import datetime

    with SessionLocal() as db:
        pending_count = db.query(Product).filter(Product.status == "pending").count()
        approved_count = db.query(Product).filter(Product.status == "approved").count()

    # احسب الموعد القادم
    now = datetime.now()
    slots = []
    for key, time_key, enabled_key in [
        ("morning", "time_morning", "schedule_morning"),
        ("afternoon", "time_afternoon", "schedule_afternoon"),
        ("evening", "time_evening", "schedule_evening"),
    ]:
        if get_setting(enabled_key, True):
            t = get_setting(time_key, "08:00")
            h, m = int(t.split(":")[0]), int(t.split(":")[1])
            slots.append((h, m, t))

    next_time = None
    for h, m, t in sorted(slots, key=lambda x: (x[0], x[1])):
        if (h, m) > (now.hour, now.minute):
            next_time = t
            break
    if not next_time and slots:
        next_time = sorted(slots, key=lambda x: (x[0], x[1]))[0][2] + " (غداً)"

    return {
        "pending_count": pending_count,
        "approved_count": approved_count,
        "next_publish_at": next_time,
        "posts_per_slot": get_setting("posts_per_slot", 1),
    }


# ── عرض اليوم ─────────────────────────────────────────────────────────────────
def _product_to_data(product) -> dict:
    return {
        "title": product.title,
        "price_original": product.price_original,
        "price_current": product.price_current,
        "discount_pct": product.discount_pct,
        "rating": product.rating,
        "rating_count": product.rating_count,
        "affiliate_url": product.affiliate_url or "",
    }


async def _regenerate_for_product(product_id: int, as_deal_of_day: bool) -> dict:
    """يُعيد توليد محتوى المنتج بحسب نوعه (deal أو regular)."""
    from backend.config import SessionLocal
    from backend.models import Product
    from backend.services.ai_service import generate_content, generate_deal_of_day_content

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        product_data = _product_to_data(product)

    if as_deal_of_day:
        content, model_used = await generate_deal_of_day_content(product_data)
    else:
        content, model_used = await generate_content(product_data)

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if product:
            product.ai_content = content
            product.ai_model_used = model_used
            db.commit()

    return {"success": True, "content": content, "model_used": model_used}


@app.post("/api/products/{product_id}/regenerate-content")
async def regenerate_content_endpoint(product_id: int):
    """يُعيد توليد نص المنتج حسب نوعه الحالي (regular أو deal-of-day)."""
    from backend.config import SessionLocal
    from backend.models import Product

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        is_deal = bool(product.is_deal_of_day)

    return await _regenerate_for_product(product_id, as_deal_of_day=is_deal)


@app.post("/api/products/{product_id}/deal-of-day")
async def set_deal_of_day(product_id: int):
    """تعيين منتج كعرض اليوم + إعادة توليد النص بقالب الـ deal تلقائياً."""
    from backend.config import SessionLocal
    from backend.models import Product

    with SessionLocal() as db:
        db.query(Product).filter(Product.is_deal_of_day == True).update({"is_deal_of_day": False})
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        product.is_deal_of_day = True
        db.commit()

    # إعادة توليد النص بقالب deal-of-day تلقائياً
    result = await _regenerate_for_product(product_id, as_deal_of_day=True)
    return {"success": True, "message": "تم تعيين عرض اليوم وإعادة توليد النص", **result}


@app.delete("/api/products/{product_id}/deal-of-day")
async def unset_deal_of_day(product_id: int):
    """إلغاء تعيين عرض اليوم + إعادة توليد النص بالقالب العادي تلقائياً."""
    from backend.config import SessionLocal
    from backend.models import Product

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404, "المنتج غير موجود")
        product.is_deal_of_day = False
        db.commit()

    # إعادة توليد النص بالقالب العادي
    result = await _regenerate_for_product(product_id, as_deal_of_day=False)
    return {"success": True, "message": "تم إلغاء عرض اليوم وإعادة توليد النص", **result}


@app.post("/api/products/{product_id}/regenerate-deal-of-day")
async def regenerate_deal_of_day_content(product_id: int):
    """[توافقية] يُعيد توليد محتوى deal-of-day صراحةً."""
    return await _regenerate_for_product(product_id, as_deal_of_day=True)


# ── الاكتشاف التلقائي ──────────────────────────────────────────────────────────
@app.post("/api/deals/auto-discover-and-queue")
async def auto_discover_and_queue_endpoint(request: Request):
    """اكتشاف تلقائي للعروض وإضافتها لقائمة الانتظار"""
    from backend.config import get_setting
    from backend.services.deals_finder_service import auto_discover_and_queue

    categories_count = get_setting("auto_discover_categories_count", 5)
    try:
        result = await auto_discover_and_queue(max_categories=categories_count)
        return {"success": True, **result}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error(f"خطأ في الاكتشاف التلقائي: {exc}")
        raise HTTPException(500, f"خطأ في الاكتشاف التلقائي: {exc}")


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
    from sqlalchemy.orm import subqueryload

    with SessionLocal() as db:
        q = db.query(Product).filter(Product.status.in_(["published", "failed", "rejected"]))
        if status:
            q = q.filter(Product.status == status)
        total = q.count()

        # تحميل publish_logs دفعة واحدة بدلاً من N+1 query
        products = (
            q.options(subqueryload(Product.publish_logs))
             .order_by(Product.created_at.desc())
             .offset((page - 1) * per_page)
             .limit(per_page)
             .all()
        )

        result = []
        for p in products:
            logs = p.publish_logs  # محمّلة مسبقاً — لا DB query
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
async def save_settings(request: Request, body: dict = Body(...)):
    from backend.config import set_setting, DEFAULT_SETTINGS
    from backend.scheduler import refresh_schedule

    # ① whitelist — فقط المفاتيح المعروفة في DEFAULT_SETTINGS
    valid_keys = set(DEFAULT_SETTINGS.keys())
    invalid = set(body.keys()) - valid_keys
    if invalid:
        raise HTTPException(400, f"مفاتيح إعدادات غير معروفة: {sorted(invalid)}")

    # ② حفظ + audit log
    user = _get_user(request)
    changed = []
    for key, value in body.items():
        set_setting(key, value)
        changed.append(key)

    if changed:
        from backend.services.audit_log import log_action_bg
        asyncio.create_task(log_action_bg(
            user_id=user.get("id"),
            action="settings.update",
            resource="settings",
            detail={"keys": changed},
            ip_address=_client_ip(request),
        ))

    refresh_schedule()
    return {"success": True, "updated": changed}


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
            # اختبار توليد فعلي بدلاً من list_models — يكشف مشاكل safety filters
            from backend.services.ai_service import _call_gemini
            text = await _call_gemini("gemini-1.5-flash", "أنت مساعد.", "قل: مرحبا")
            preview = (text or "").strip()[:30]
            return {"success": True, "message": f"متصل ✓ — Gemini يولّد ({preview})"}
        except Exception as e:
            return {"success": False, "message": f"الاتصال نجح لكن التوليد فشل: {e}"}

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
            # جرّب Gemini 2.0 Flash المجاني أولاً، ثم Llama
            for model in [
                "openrouter/google/gemini-2.0-flash-exp:free",
                "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                "openrouter/meta-llama/llama-3.3-70b-instruct",
            ]:
                result = await test_model(model)
                if result.get("success"):
                    return result
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


# ── تشخيص شامل للذكاء الاصطناعي ──────────────────────────────────────────────
@app.get("/api/ai/diagnostics")
async def ai_diagnostics():
    """يكشف لماذا يقع التوليد على القالب الجاهز:
    أي مزوّد لديه مفتاح، أي نماذج جاهزة، ونتيجة محاولة فعلية للأول.
    """
    from backend.services.ai_service import diagnose_ai
    return await diagnose_ai()


# ── سجل التدقيق (admin only) ─────────────────────────────────────────────────
@app.get("/api/audit-logs")
async def get_audit_logs(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    action_prefix: Optional[str] = None,
):
    _require_admin(request)
    from backend.services.audit_log import get_recent_logs
    return {"logs": get_recent_logs(limit=limit, action_prefix=action_prefix)}


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

    if len(url) > 2048:
        raise HTTPException(400, "الرابط طويل جداً (الحد 2048 حرف)")

    # ① بناء رابط الإحالة (يُضيف tag= تلقائياً إذا لم يكن موجوداً)
    affiliate_url = resolve_affiliate_url(url)

    # ② جلب بيانات المنتج من الصفحة (مع SSRF protection)
    try:
        fetched = await fetch_product_data(url)
    except ValueError as exc:
        raise HTTPException(400, f"رابط غير مدعوم: {exc}")

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
    import json as _json
    from backend.config import SessionLocal
    from backend.models import DiscoveryCategory
    with SessionLocal() as db:
        rows = db.query(DiscoveryCategory).order_by(DiscoveryCategory.sort_order).all()
        return [
            {
                "id": r.cat_id, "name_ar": r.name_ar, "icon": r.icon,
                "keywords": _json.loads(r.keywords), "active": r.active,
                "db_id": r.id, "sort_order": r.sort_order,
            }
            for r in rows
        ]


class CategoryBody(BaseModel):
    name_ar: str
    icon: str = "🛍️"
    keywords: list[str]
    active: bool = True


@app.post("/api/deals/categories")
async def create_category(body: CategoryBody, request: Request):
    """إضافة تصنيف جديد"""
    import json as _json, re
    from backend.config import SessionLocal
    from backend.models import DiscoveryCategory
    # توليد cat_id من الاسم
    cat_id = re.sub(r"[^a-z0-9_]", "", body.name_ar.lower().replace(" ", "_").replace("-", "_"))[:50]
    if not cat_id:
        cat_id = f"cat_{int(__import__('time').time())}"
    with SessionLocal() as db:
        if db.query(DiscoveryCategory).filter(DiscoveryCategory.cat_id == cat_id).first():
            cat_id = f"{cat_id}_{int(__import__('time').time())}"
        max_order = db.query(DiscoveryCategory).count()
        row = DiscoveryCategory(
            cat_id=cat_id, name_ar=body.name_ar, icon=body.icon,
            keywords=_json.dumps(body.keywords, ensure_ascii=False),
            active=body.active, sort_order=max_order + 1,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"success": True, "id": row.id, "cat_id": row.cat_id}


@app.put("/api/deals/categories/{db_id}")
async def update_category(db_id: int, body: CategoryBody, request: Request):
    """تعديل تصنيف موجود"""
    import json as _json
    from backend.config import SessionLocal
    from backend.models import DiscoveryCategory
    with SessionLocal() as db:
        row = db.get(DiscoveryCategory, db_id)
        if not row:
            raise HTTPException(404, "التصنيف غير موجود")
        row.name_ar = body.name_ar
        row.icon = body.icon
        row.keywords = _json.dumps(body.keywords, ensure_ascii=False)
        row.active = body.active
        db.commit()
        return {"success": True}


@app.delete("/api/deals/categories/{db_id}")
async def delete_category(db_id: int, request: Request):
    """حذف تصنيف"""
    from backend.config import SessionLocal
    from backend.models import DiscoveryCategory
    with SessionLocal() as db:
        row = db.get(DiscoveryCategory, db_id)
        if not row:
            raise HTTPException(404, "التصنيف غير موجود")
        db.delete(row)
        db.commit()
        return {"success": True}


class DiscoverDealsBody(BaseModel):
    category_ids: list[str]
    seen_asins: Optional[dict] = None  # {category_id: [asin, ...]} — يُستبعد من النتائج


@app.post("/api/deals/discover")
async def discover_deals(body: DiscoverDealsBody, request: Request):
    """اكتشاف أفضل العروض للتصنيفات المحددة باستخدام RapidAPI + AI"""
    if not body.category_ids:
        raise HTTPException(400, "يجب اختيار تصنيف واحد على الأقل")
    if len(body.category_ids) > 8:
        raise HTTPException(400, "الحد الأقصى 8 تصنيفات في البحث الواحد")
    try:
        from backend.services.deals_finder_service import discover_deals as _discover
        results = await _discover(body.category_ids, seen_asins=body.seen_asins)
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
    from backend.services.amazon_service import is_product_posted_before

    published_count = 0
    skipped_count = 0
    results = []

    for deal in body.deals:
        asin = deal.get("asin", "")
        try:
            # تخطي المنتجات المنشورة سابقاً
            if asin and await is_product_posted_before(asin):
                skipped_count += 1
                results.append({"asin": asin, "success": False, "skipped": True, "error": "تم نشر هذا المنتج مسبقاً"})
                continue

            title = deal.get("title", "")
            with SessionLocal() as db:
                product = Product(
                    title=title,
                    title_ar=deal.get("title_ar") or title,
                    source="deals_finder",
                    amazon_asin=asin,
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
            results.append({"asin": asin, "success": True, "product_id": product_id, **pub_result})
        except Exception as exc:
            logger.error(f"خطأ في نشر المنتج {asin}: {exc}")
            results.append({"asin": asin, "success": False, "error": str(exc)})

    return {
        "success": True,
        "published": published_count,
        "skipped": skipped_count,
        "total": len(body.deals),
        "results": results,
    }


class AddToQueueBody(BaseModel):
    deals: list[dict]


@app.post("/api/deals/add-to-queue")
async def add_deals_to_queue(body: AddToQueueBody, request: Request):
    """حفظ العروض في قائمة الانتظار (status=pending) بدون نشر فوري"""
    if not body.deals:
        raise HTTPException(400, "لا توجد منتجات للإضافة")

    from backend.config import SessionLocal
    from backend.models import Product
    from backend.services.amazon_service import is_product_posted_before

    added_count = 0
    skipped_count = 0
    results = []

    for deal in body.deals:
        asin = deal.get("asin", "")
        try:
            if asin and await is_product_posted_before(asin):
                skipped_count += 1
                results.append({"asin": asin, "success": False, "skipped": True, "error": "تم نشر هذا المنتج مسبقاً"})
                continue

            title = deal.get("title", "")
            with SessionLocal() as db:
                product = Product(
                    title=title,
                    title_ar=deal.get("title_ar") or title,
                    source="deals_finder",
                    amazon_asin=asin,
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
                    status="pending",
                )
                db.add(product)
                db.commit()
                db.refresh(product)
                product_id = product.id

            added_count += 1
            results.append({"asin": asin, "success": True, "product_id": product_id})
        except Exception as exc:
            logger.error(f"خطأ في إضافة المنتج {asin} لقائمة الانتظار: {exc}")
            results.append({"asin": asin, "success": False, "error": str(exc)})

    return {
        "success": True,
        "added": added_count,
        "skipped": skipped_count,
        "total": len(body.deals),
        "results": results,
    }


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
        "is_deal_of_day": getattr(p, "is_deal_of_day", False) or False,
        "total_clicks": getattr(p, "total_clicks", 0) or 0,
        "telegram_post_id": getattr(p, "telegram_post_id", None),
        "x_tweet_id": getattr(p, "x_tweet_id", None),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
    }


# ── Entry point ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
