"""
admin_panel/main.py — لوحة المدير المركزية للـ SaaS.
تعمل كـ instance منفصلة عن instances العملاء.

المسؤوليات:
  • تسجيل دخول مالك المنصة (Operator) — منفصل عن admin instances العملاء
  • CRUD للعملاء (إنشاء/تعليق/إلغاء)
  • عرض إحصائيات الاستخدام
  • استقبال ويب هوكس Stripe (إن فُعّل)
  • تشغيل سكريبت إنشاء instance جديدة عند التسجيل

تشغيل:
  uvicorn admin_panel.main:app --host 0.0.0.0 --port 8001
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Body, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from admin_panel.models import (
    Client, UsageLog, Operator, MasterSession, init_master_db, SUBSCRIPTION_PLANS,
)
from admin_panel.auth import (
    authenticate_operator, create_token, get_operator_from_token,
    create_default_owner,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("admin_panel")

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "create_client.py"
STATIC_DIR = Path(__file__).parent / "static"
IS_PRODUCTION = os.getenv("ENVIRONMENT", "").lower() == "production"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_master_db()
    try:
        create_default_owner()
    except Exception as exc:
        logger.error(f"فشل إنشاء حساب المالك الافتراضي: {exc}")
    logger.info("✓ Admin Panel جاهز")
    yield


app = FastAPI(title="Affiliate SaaS — Admin Panel", lifespan=lifespan)

# CORS — مقيّد على لوحة الأدمن فقط
admin_origins = [
    o.strip() for o in os.getenv("ADMIN_ALLOWED_ORIGINS", "http://localhost:8001").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=admin_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Key"],
)

# ── ملفات ثابتة (HTML للـ login والـ dashboard) ────────────────────────────
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── المسارات العامة (لا تحتاج مصادقة) ─────────────────────────────────────
_PUBLIC_PATHS = {
    "/", "/health", "/login", "/api/auth/login", "/webhook/stripe",
}
_PUBLIC_PREFIXES = ("/static/",)


# ── Auth: JWT cookie للمالك + X-Admin-Key للوصول البرمجي ────────────────
@app.middleware("http")
async def admin_auth(request: Request, call_next):
    path = request.url.path

    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    # ① JWT cookie أولاً (تسجيل دخول الـ UI)
    token = request.cookies.get("operator_token")
    if token:
        operator = get_operator_from_token(token)
        if operator:
            request.state.operator = operator
            return await call_next(request)

    # ② API key (للوصول البرمجي/CLI/scripts)
    if ADMIN_API_KEY and request.headers.get("X-Admin-Key") == ADMIN_API_KEY:
        request.state.operator = {"id": 0, "username": "api-key", "role": "super_admin"}
        return await call_next(request)

    # ③ غير مصادق
    if path.startswith("/api/"):
        return JSONResponse({"detail": "غير مصرّح"}, status_code=401)
    return RedirectResponse(url="/login", status_code=302)


# ── Health ────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── الصفحة الرئيسية: redirect حسب حالة الجلسة ──────────────────────────
@app.get("/")
async def root(request: Request):
    token = request.cookies.get("operator_token")
    if token and get_operator_from_token(token):
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login")
async def login_page():
    f = STATIC_DIR / "login.html"
    if f.exists():
        return FileResponse(str(f))
    return JSONResponse({"detail": "صفحة الدخول غير موجودة"}, status_code=404)


@app.get("/dashboard")
async def dashboard_page():
    f = STATIC_DIR / "dashboard.html"
    if f.exists():
        return FileResponse(str(f))
    return JSONResponse({"detail": "صفحة لوحة التحكم غير موجودة"}, status_code=404)


# ── تسجيل الدخول/الخروج ───────────────────────────────────────────────────
class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def login(body: LoginBody, response: Response):
    operator = authenticate_operator(body.username, body.password)
    if not operator:
        raise HTTPException(401, "اسم المستخدم أو كلمة المرور غير صحيحة")

    token = create_token(operator["id"], operator["username"], operator["role"])
    response.set_cookie(
        key="operator_token",
        value=token,
        httponly=True,
        samesite="strict" if IS_PRODUCTION else "lax",
        secure=IS_PRODUCTION,
        max_age=60 * 60 * 24 * 7,
    )
    return {"success": True, "operator": operator}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("operator_token")
    return {"success": True}


@app.get("/api/auth/me")
async def me(request: Request):
    op = getattr(request.state, "operator", None)
    if not op:
        raise HTTPException(401, "غير مصادق")
    return {"operator": op}


# ── خطط الاشتراك ──────────────────────────────────────────────────────────
@app.get("/api/plans")
async def list_plans():
    return SUBSCRIPTION_PLANS


# ── إدارة العملاء ─────────────────────────────────────────────────────────
class CreateClientBody(BaseModel):
    name: str
    email: str
    plan: str = "free"


@app.get("/api/clients")
async def list_clients(status: Optional[str] = None, plan: Optional[str] = None):
    with MasterSession() as db:
        q = db.query(Client)
        if status:
            q = q.filter(Client.status == status)
        if plan:
            q = q.filter(Client.plan == plan)
        clients = q.order_by(Client.created_at.desc()).all()
        return {
            "total": len(clients),
            "items": [_client_dict(c) for c in clients],
        }


@app.post("/api/clients")
async def create_client(body: CreateClientBody):
    """ينشئ عميل جديد ويُطلق سكريبت إنشاء instance على Render في الخلفية."""
    if body.plan not in SUBSCRIPTION_PLANS:
        raise HTTPException(400, f"خطة غير صالحة: {body.plan}")

    with MasterSession() as db:
        existing = db.query(Client).filter(Client.email == body.email.lower()).first()
        if existing:
            raise HTTPException(409, "البريد الإلكتروني مسجّل مسبقاً")

    asyncio.create_task(_run_provisioning_script(body))
    return {
        "success": True,
        "message": "بدأ إنشاء instance جديدة. ستظهر في القائمة بعد 5-10 دقائق.",
    }


async def _run_provisioning_script(body: CreateClientBody):
    """يُشغّل scripts/create_client.py في subprocess."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(SCRIPT_PATH),
            "--client-name", body.name,
            "--email", body.email,
            "--plan", body.plan,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            logger.info(f"تم إنشاء العميل {body.email}")
        else:
            logger.error(f"فشل إنشاء {body.email}: {stderr.decode(errors='replace')}")
    except Exception as exc:
        logger.error(f"خطأ في تشغيل السكريبت لـ {body.email}: {exc}")


@app.put("/api/clients/{client_id}/suspend")
async def suspend_client(client_id: int):
    return _change_status(client_id, "suspended")


@app.put("/api/clients/{client_id}/activate")
async def activate_client(client_id: int):
    return _change_status(client_id, "active")


@app.put("/api/clients/{client_id}/cancel")
async def cancel_client(client_id: int):
    return _change_status(client_id, "cancelled")


def _change_status(client_id: int, new_status: str) -> dict:
    with MasterSession() as db:
        client = db.get(Client, client_id)
        if not client:
            raise HTTPException(404, "العميل غير موجود")
        client.status = new_status
        if new_status == "suspended":
            client.suspended_at = datetime.utcnow()
        elif new_status == "active":
            client.activated_at = datetime.utcnow()
            client.suspended_at = None
        db.commit()
        return {"success": True, "client": _client_dict(client)}


@app.get("/api/clients/{client_id}/usage")
async def client_usage(client_id: int, period: Optional[str] = None):
    """إحصائيات استهلاك العميل لشهر محدد (تنسيق: 2026-04)."""
    period = period or datetime.utcnow().strftime("%Y-%m")
    with MasterSession() as db:
        client = db.get(Client, client_id)
        if not client:
            raise HTTPException(404, "العميل غير موجود")
        usage = (
            db.query(UsageLog)
              .filter(UsageLog.client_id == client_id, UsageLog.period == period)
              .first()
        )
        plan_info = SUBSCRIPTION_PLANS.get(client.plan, {})
        return {
            "client": _client_dict(client),
            "period": period,
            "usage": {
                "products_published": usage.products_published if usage else 0,
                "ai_calls": usage.ai_calls if usage else 0,
                "api_calls": usage.api_calls if usage else 0,
            },
            "limits": {
                "products_per_month": plan_info.get("products_per_month", 0),
                "ai_calls_per_month": plan_info.get("ai_calls_per_month", 0),
            },
        }


# ── webhook Stripe (هيكل أساسي) ──────────────────────────────────────────
@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    # TODO: التحقق من توقيع Stripe-Signature header قبل معالجة الـ event
    logger.info(f"استلام Stripe webhook: {len(payload)} byte")
    return {"received": True}


# ── Helpers ──────────────────────────────────────────────────────────────
def _client_dict(c: Client) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "plan": c.plan,
        "status": c.status,
        "service_url": c.service_url,
        "monthly_fee": c.monthly_fee,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "activated_at": c.activated_at.isoformat() if c.activated_at else None,
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
    }
