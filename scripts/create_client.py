#!/usr/bin/env python3
"""
create_client.py — ينشئ instance منفصلة على Render لعميل جديد.

يتطلب متغيرات البيئة:
  RENDER_API_KEY      — من Render Account Settings → API Keys
  RENDER_OWNER_ID     — معرّف الـ team/user owner
  TEMPLATE_REPO_URL   — رابط الريبو (مثلاً: https://github.com/USER/Affiliate)
  MASTER_DATABASE_URL — اختياري، قاعدة المدير المركزية لتسجيل العميل

الاستخدام:
  python scripts/create_client.py \\
      --client-name "Ahmed Store" \\
      --email ahmed@example.com \\
      --plan pro

ما يفعله بالترتيب:
  1. ينشئ Postgres database مجانية على Render
  2. ينتظر حتى تجهز
  3. ينشئ web service جديد مرتبط بنفس الريبو
  4. يربط DATABASE_URL تلقائياً بقاعدة Postgres الجديدة
  5. يولّد ENCRYPTION_KEY فريد للعميل
  6. يحفظ بيانات العميل في قاعدة المدير المركزية (إن وُجدت)
"""
import argparse
import logging
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# إضافة root للـ imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("create_client")

RENDER_API = "https://api.render.com/v1"
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID", "")
TEMPLATE_REPO_URL = os.getenv("TEMPLATE_REPO_URL", "")


def _headers() -> dict:
    if not RENDER_API_KEY:
        raise RuntimeError("RENDER_API_KEY غير مضبوط")
    return {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Content-Type": "application/json",
    }


def _slugify(text: str) -> str:
    """ينظّف الاسم ليكون مناسباً كـ slug على Render."""
    import re
    s = re.sub(r"[^a-z0-9-]+", "-", text.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "client"


def create_postgres(client_slug: str) -> dict:
    """ينشئ Postgres database مجانية ويعيد الـ connection details."""
    payload = {
        "name": f"affiliate-db-{client_slug}",
        "ownerId": RENDER_OWNER_ID,
        "plan": "free",
        "region": "frankfurt",
        "databaseName": "affiliate",
        "databaseUser": "affiliate",
    }
    r = requests.post(f"{RENDER_API}/postgres", json=payload, headers=_headers(), timeout=30)
    r.raise_for_status()
    db = r.json()
    logger.info(f"تم إنشاء قاعدة Postgres: {db['id']}")
    return db


def wait_for_postgres(db_id: str, timeout: int = 300) -> str:
    """ينتظر حتى تجهز قاعدة Postgres ويعيد الـ connection string."""
    logger.info("انتظار تجهيز قاعدة البيانات...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{RENDER_API}/postgres/{db_id}", headers=_headers(), timeout=15)
        r.raise_for_status()
        db = r.json()
        status = db.get("status")
        if status == "available":
            r2 = requests.get(
                f"{RENDER_API}/postgres/{db_id}/connection-info",
                headers=_headers(), timeout=15,
            )
            r2.raise_for_status()
            info = r2.json()
            logger.info("قاعدة البيانات جاهزة ✓")
            return info.get("internalConnectionString") or info.get("externalConnectionString", "")
        time.sleep(10)
    raise TimeoutError(f"تجاوز انتظار قاعدة البيانات {timeout} ثانية")


def create_web_service(
    client_slug: str,
    database_url: str,
    encryption_key: str,
    allowed_origin: Optional[str] = None,
) -> dict:
    """ينشئ web service جديد على Render مع الإعدادات المناسبة."""
    if not TEMPLATE_REPO_URL:
        raise RuntimeError("TEMPLATE_REPO_URL غير مضبوط")

    env_vars = [
        {"key": "DATABASE_URL", "value": database_url},
        {"key": "ENCRYPTION_KEY", "value": encryption_key},
        {"key": "ENVIRONMENT", "value": "production"},
        {"key": "PYTHON_VERSION", "value": "3.11.0"},
    ]
    if allowed_origin:
        env_vars.append({"key": "ALLOWED_ORIGINS", "value": allowed_origin})

    payload = {
        "type": "web_service",
        "name": f"affiliate-{client_slug}",
        "ownerId": RENDER_OWNER_ID,
        "repo": TEMPLATE_REPO_URL,
        "branch": "main",
        "autoDeploy": "yes",
        "serviceDetails": {
            "env": "python",
            "plan": "free",
            "region": "frankfurt",
            "healthCheckPath": "/api/health",
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "uvicorn backend.main:app --host 0.0.0.0 --port $PORT",
            },
            "envVars": env_vars,
        },
    }
    r = requests.post(f"{RENDER_API}/services", json=payload, headers=_headers(), timeout=60)
    r.raise_for_status()
    service = r.json()
    logger.info(f"تم إنشاء الخدمة: {service.get('service', service).get('id', 'unknown')}")
    return service


def register_in_master_db(
    name: str,
    email: str,
    plan: str,
    render_service_id: str,
    render_db_id: str,
    service_url: str,
) -> Optional[int]:
    """يسجّل العميل في قاعدة المدير المركزية (إن وُجدت)."""
    if not os.getenv("MASTER_DATABASE_URL"):
        logger.info("MASTER_DATABASE_URL غير مضبوط — تخطي تسجيل العميل")
        return None

    try:
        from admin_panel.models import Client, MasterSession, init_master_db, SUBSCRIPTION_PLANS
        from passlib.context import CryptContext

        init_master_db()
        plan_info = SUBSCRIPTION_PLANS.get(plan, SUBSCRIPTION_PLANS["free"])
        # كلمة مرور مؤقتة — ترسل للعميل بريدياً
        temp_password = secrets.token_urlsafe(12)
        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        pw_hash = ctx.hash(temp_password)

        with MasterSession() as db:
            client = Client(
                name=name,
                email=email.lower().strip(),
                password_hash=pw_hash,
                plan=plan,
                status="active",
                render_service_id=render_service_id,
                render_db_id=render_db_id,
                service_url=service_url,
                monthly_fee=plan_info["price_usd"],
                activated_at=datetime.utcnow(),
            )
            db.add(client)
            db.commit()
            db.refresh(client)
            logger.info(f"تم تسجيل العميل #{client.id} — كلمة مرور مؤقتة: {temp_password}")
            return client.id
    except Exception as exc:
        logger.error(f"فشل تسجيل العميل في قاعدة المدير: {exc}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-name", required=True, help="اسم العميل")
    parser.add_argument("--email", required=True, help="بريد العميل الإلكتروني")
    parser.add_argument("--plan", default="free", choices=["free", "pro", "enterprise"])
    args = parser.parse_args()

    if not RENDER_API_KEY or not RENDER_OWNER_ID:
        logger.error("اضبط RENDER_API_KEY و RENDER_OWNER_ID")
        return 1

    client_slug = _slugify(args.client_name)
    logger.info(f"بدء إنشاء عميل: {args.client_name} ({client_slug})")

    # 1. Postgres
    db = create_postgres(client_slug)
    db_url = wait_for_postgres(db["id"])

    # 2. Web service
    encryption_key = secrets.token_urlsafe(32)
    service = create_web_service(client_slug, db_url, encryption_key)
    service_data = service.get("service", service)
    service_id = service_data.get("id", "")
    service_url = service_data.get("serviceDetails", {}).get("url") \
        or f"https://affiliate-{client_slug}.onrender.com"

    # 3. تسجيل في قاعدة المدير
    client_id = register_in_master_db(
        name=args.client_name,
        email=args.email,
        plan=args.plan,
        render_service_id=service_id,
        render_db_id=db["id"],
        service_url=service_url,
    )

    print("\n" + "═" * 60)
    print(f"✓ تم إنشاء عميل جديد بنجاح")
    print("═" * 60)
    print(f"  الاسم:   {args.client_name}")
    print(f"  البريد: {args.email}")
    print(f"  الخطة:   {args.plan}")
    print(f"  الرابط:  {service_url}")
    print(f"  Service: {service_id}")
    print(f"  DB ID:   {db['id']}")
    if client_id:
        print(f"  Master client_id: {client_id}")
    print("═" * 60)
    print("ملاحظة: قد تحتاج بضع دقائق إضافية حتى يكتمل أول build على Render.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
