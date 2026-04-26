"""
admin_panel/main.py — لوحة المدير المركزية للـ SaaS.
تعمل كـ instance منفصلة عن instances العملاء.

المسؤوليات:
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
import secrets
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Body, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from admin_panel.models import (
    Client, UsageLog, MasterSession, init_master_db, SUBSCRIPTION_PLANS,
)
from passlib.context import CryptContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("admin_panel")

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "create_client.py"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_master_db()
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


# ── Auth بسيط: API key في header (لا حاجة لـ JWT في الأدمن) ──────────────
@app.middleware("http")
async def admin_auth(request: Request, call_next):
    if request.url.path in ("/health", "/webhook/stripe"):
        return await call_next(request)
    if not ADMIN_API_KEY:
        return JSONResponse({"detail": "ADMIN_API_KEY غير مضبوط في الخادم"}, status_code=500)
    if request.headers.get("X-Admin-Key") != ADMIN_API_KEY:
        return JSONResponse({"detail": "غير مصرّح"}, status_code=401)
    return await call_next(request)


# ── Health ────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


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

    # تشغيل السكريبت في الخلفية — لا ننتظر اكتماله (10-15 دقيقة عادةً)
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
        # TODO: استدعاء Render API لإيقاف/تشغيل الـ service
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
    """Webhook لاستقبال أحداث الفواتير من Stripe.
    يُفعّل/يعلّق العميل تلقائياً حسب حالة الدفع.
    """
    payload = await request.body()
    # TODO: التحقق من توقيع Stripe-Signature header قبل معالجة الـ event
    # event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
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
