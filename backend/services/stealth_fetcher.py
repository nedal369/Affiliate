"""
stealth_fetcher.py — طبقة موحّدة لتجاوز حظر أمازون عبر Scrapling

تقدّم بوابة واحدة `stealth_fetch(url, mode)` تُستخدم من amazon_scraper و link_fetcher
كطبقة احتياط عند فشل aiohttp بسبب الحظر.

المستويات:
- mode="fast"    → Scrapling Fetcher (curl_cffi + TLS impersonation كـ Chrome)
- mode="stealth" → Scrapling StealthyFetcher (متصفّح Camoufox headless كامل)
- mode="auto"    → fast أولاً؛ عند الحظر ينتقل تلقائياً لـ stealth

الأمان:
- lazy import: لا نستورد scrapling على مستوى الموديول حتى لا يفشل التشغيل
  إذا لم تكن المكتبة مثبّتة (يستمر التطبيق بالعمل عبر aiohttp فقط).
- fallback نظيف: عند فشل الاستيراد أو غياب Camoufox، نُرجع ("", -1).
- Semaphore منفصل للـ stealth لتحديد استهلاك الذاكرة (المتصفّح ثقيل).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_STEALTH_SEMAPHORE: Optional[asyncio.Semaphore] = None
_FAST_SEMAPHORE: Optional[asyncio.Semaphore] = None

_scrapling_available: Optional[bool] = None
_stealth_available: Optional[bool] = None


def _get_stealth_semaphore() -> asyncio.Semaphore:
    global _STEALTH_SEMAPHORE
    if _STEALTH_SEMAPHORE is None:
        _STEALTH_SEMAPHORE = asyncio.Semaphore(2)
    return _STEALTH_SEMAPHORE


def _get_fast_semaphore() -> asyncio.Semaphore:
    global _FAST_SEMAPHORE
    if _FAST_SEMAPHORE is None:
        _FAST_SEMAPHORE = asyncio.Semaphore(4)
    return _FAST_SEMAPHORE


def _check_scrapling() -> bool:
    global _scrapling_available
    if _scrapling_available is not None:
        return _scrapling_available
    try:
        import scrapling  # noqa: F401
        _scrapling_available = True
        logger.info("Scrapling متاح — سيُستخدم كطبقة احتياطية ضد الحظر")
    except ImportError:
        _scrapling_available = False
        logger.warning("Scrapling غير مثبّت — pip install 'scrapling[fetchers]' لتفعيله")
    return _scrapling_available


def _extract_html(response) -> str:
    """يستخرج HTML من Scrapling Response (لا يحتوي على .status)."""
    html = getattr(response, "body", None) or getattr(response, "html_content", None) or ""
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    return html


def is_blocked_html(html: str, status: int = 200) -> bool:
    """منطق كشف الحظر — نفس المعايير المستخدمة في amazon_scraper._is_blocked."""
    if status in (403, 503):
        return True
    if not html or len(html) < 5000:
        return True
    lower = html.lower()
    return any(x in lower for x in (
        "robot check", "validatecaptcha", "captcha", "api-services-support",
        "automated access", "unusual traffic", "verify you are human",
        "sorry, we just need to make sure",
    ))


async def _fetch_fast(url: str, timeout: float = 20.0) -> tuple[str, int]:
    """
    Scrapling Fetcher — curl_cffi مع بصمة TLS لـ Chrome.
    يُرجع (html, 200) عند النجاح أو ("", -1) عند الفشل.
    Note: Fetcher.get مزامن — نشغّله في thread لتفادي حجب event loop.
    """
    if not _check_scrapling():
        return "", -1

    try:
        from scrapling.fetchers import Fetcher
    except Exception as exc:
        logger.warning(f"Scrapling Fetcher import فشل: {exc}")
        return "", -1

    async with _get_fast_semaphore():
        try:
            response = await asyncio.to_thread(
                Fetcher.get,
                url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=int(timeout),
            )
            html = _extract_html(response)
            status = 200 if html else 0
            return html, status
        except Exception as exc:
            logger.warning(f"Scrapling fast فشل {url}: {exc}")
            return "", -1


async def _fetch_stealth(url: str, timeout: float = 45.0) -> tuple[str, int]:
    """
    Scrapling StealthyFetcher — متصفّح Camoufox headless كامل.
    يستخدم async_fetch الأصلي (لا asyncio.to_thread).
    timeout بالثواني (وليس ms).
    """
    global _stealth_available
    if not _check_scrapling():
        return "", -1

    if _stealth_available is False:
        return "", -1

    try:
        from scrapling.fetchers import StealthyFetcher
    except Exception as exc:
        _stealth_available = False
        logger.warning(f"StealthyFetcher import فشل (شغّل 'scrapling install'): {exc}")
        return "", -1

    async with _get_stealth_semaphore():
        try:
            response = await StealthyFetcher.async_fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=int(timeout),   # ثواني (وليس ms)
                humanize=True,
            )
            html = _extract_html(response)
            status = 200 if html else 0
            if _stealth_available is None:
                _stealth_available = True
            return html, status
        except Exception as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("camoufox", "executable", "playwright", "browser", "binary")):
                _stealth_available = False
                logger.warning(
                    f"StealthyFetcher: المتصفّح غير مثبّت ({exc}). "
                    "شغّل 'python -m scrapling install' لتنزيله."
                )
            else:
                logger.warning(f"Scrapling stealth فشل {url}: {exc}")
            return "", -1


async def stealth_fetch(url: str, mode: str = "auto", timeout: float = 20.0) -> tuple[str, int]:
    """
    البوابة الرئيسية — تُرجع (html, status).
    mode: "fast" | "stealth" | "auto"
    عند فشل الاستيراد أو الجلب: ("", -1) ليُميّزها المتّصل عن الحظر العادي.
    """
    if mode == "fast":
        return await _fetch_fast(url, timeout=timeout)

    if mode == "stealth":
        return await _fetch_stealth(url, timeout=max(timeout, 45.0))

    # auto: fast أولاً، ثم stealth عند الحظر/الفشل
    html, status = await _fetch_fast(url, timeout=timeout)
    if html and not is_blocked_html(html, status):
        logger.info(f"stealth_fetch: fast نجح ({len(html)} chars): {url}")
        return html, status

    logger.info(f"stealth_fetch: fast فشل ({status}, {len(html)} chars) — التحوّل لـ stealth: {url}")
    return await _fetch_stealth(url, timeout=max(timeout, 45.0))


async def diagnose(url: str) -> dict:
    """يُجرّب الطبقات الثلاث ويُرجع تقريراً تشخيصياً (للاستخدام في /api/scraper/diagnose)."""
    import time as _t
    report: dict = {"url": url, "scrapling_installed": _check_scrapling(), "layers": []}

    t0 = _t.time()
    html, status = await _fetch_fast(url)
    report["layers"].append({
        "name": "scrapling_fast",
        "status": status,
        "html_length": len(html),
        "elapsed_s": round(_t.time() - t0, 2),
        "blocked": is_blocked_html(html, status),
        "success": bool(html and not is_blocked_html(html, status)),
    })

    t0 = _t.time()
    html, status = await _fetch_stealth(url)
    report["layers"].append({
        "name": "scrapling_stealth",
        "status": status,
        "html_length": len(html),
        "elapsed_s": round(_t.time() - t0, 2),
        "blocked": is_blocked_html(html, status),
        "success": bool(html and not is_blocked_html(html, status)),
    })

    return report
