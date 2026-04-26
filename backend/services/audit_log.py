"""
audit_log.py — تسجيل العمليات الحساسة
يستدعى من endpoints تغيير الإعدادات، المفاتيح، الموافقة/رفض المنتجات، إلخ.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def log_action(
    user_id: Optional[int],
    action: str,
    resource: Optional[str] = None,
    resource_id: Optional[str] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """يُسجّل عملية في جدول audit_logs.
    لا يرفع استثناءات — فشل الـ logging لا يجب أن يُفشل العملية الأصلية.
    """
    try:
        from backend.config import SessionLocal
        from backend.models import AuditLog

        with SessionLocal() as db:
            entry = AuditLog(
                user_id=user_id,
                action=action[:100] if action else "",
                resource=resource[:50] if resource else None,
                resource_id=str(resource_id)[:50] if resource_id else None,
                detail=json.dumps(detail, ensure_ascii=False, default=str)[:5000] if detail else None,
                ip_address=ip_address[:45] if ip_address else None,
            )
            db.add(entry)
            db.commit()
    except Exception as exc:
        logger.warning(f"فشل تسجيل audit log [{action}]: {exc}")


def get_recent_logs(
    limit: int = 100,
    user_id: Optional[int] = None,
    action_prefix: Optional[str] = None,
) -> list[dict]:
    """يجلب آخر العمليات المسجّلة. يُستخدم في endpoint الـ admin."""
    from backend.config import SessionLocal
    from backend.models import AuditLog

    with SessionLocal() as db:
        q = db.query(AuditLog)
        if user_id is not None:
            q = q.filter(AuditLog.user_id == user_id)
        if action_prefix:
            q = q.filter(AuditLog.action.like(f"{action_prefix}%"))
        rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "action": r.action,
                "resource": r.resource,
                "resource_id": r.resource_id,
                "detail": json.loads(r.detail) if r.detail else None,
                "ip_address": r.ip_address,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
