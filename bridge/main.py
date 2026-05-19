"""
Haier AC Web Server
SmartHQ bridge + web UI in one process.

Start:  uvicorn main:app --host 0.0.0.0 --port 8765
Config: copy .env.example → .env and fill in credentials.
Open:   http://<server-ip>:8765

Auth:   Login required for every request. Set AUTH_USERNAME / AUTH_PASSWORD
        in .env. Put behind an HTTPS reverse proxy (Caddy/Nginx) and set
        SECURE_COOKIES=true before exposing externally.
"""
import asyncio
import hashlib
import hmac
import os
import socket
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from urllib.parse import urlparse
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import httpx
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger(__name__)

USERNAME  = os.getenv("SMARTHQ_USERNAME", "").strip()
PASSWORD  = os.getenv("SMARTHQ_PASSWORD", "").strip()
DEVICE_ID = os.getenv("SMARTHQ_DEVICE_ID", "").strip()
REGION    = os.getenv("SMARTHQ_REGION", "US").strip()

_client = None
_timer_task: asyncio.Task | None = None
_timer_ends_at: datetime | None = None
_refresh_task: asyncio.Task | None = None

REFRESH_INTERVAL_SECONDS = 60


async def _periodic_refresh():
    """Re-request ERD values every minute so a stale/corrupt cached reading
    (e.g. the 18xxx target-temp glitch after long idle on fan mode) gets
    overwritten by a fresh value from the device."""
    while True:
        try:
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
            if _client is not None:
                await _client.request_refresh()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("Periodic refresh error: %s", e)


def _get_lan_ip() -> str:
    """Return the LAN IP via the default-route interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


async def _get_public_ip() -> str | None:
    """Return the public IP from api.ipify.org, or None on failure."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://api.ipify.org", timeout=3.0)
            return r.text.strip()
    except Exception:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _refresh_task
    if USERNAME and PASSWORD:
        from haier_smarthq import SmartHQClient
        _client = SmartHQClient(USERNAME, PASSWORD, DEVICE_ID, REGION)
        await _client.start()
        log.info("SmartHQ connected. Device ID: %s", DEVICE_ID or "(see /api/devices)")
        _refresh_task = asyncio.create_task(_periodic_refresh())
    else:
        log.warning("SMARTHQ_USERNAME / SMARTHQ_PASSWORD not set — edit .env and restart.")

    lan_ip = _get_lan_ip()
    log.info("Local  (LAN)  ->  http://%s:8765", lan_ip)
    if os.getenv("ADVERTISE_PUBLIC_IP", "").lower() in ("1", "true", "yes"):
        public_ip = await _get_public_ip()
        if public_ip:
            log.info("External     ->  http://%s:8765  (needs port 8765 forwarded on router)", public_ip)

    yield
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
    if _timer_task and not _timer_task.done():
        _timer_task.cancel()
    if _client:
        try:
            await asyncio.wait_for(_client.stop(), timeout=10)
        except asyncio.TimeoutError:
            log.warning("SmartHQ stop exceeded 10s — abandoning")
        except Exception:
            log.exception("SmartHQ stop raised — abandoning")


app = FastAPI(title="Haier AC", lifespan=lifespan)

# Auth middleware
from auth import (AuthMiddleware, AUTH_USERNAME, AUTH_PASSWORD,
                  COOKIE_NAME, CSRF_COOKIE, COOKIE_DAYS, SECURE_COOKIES,
                  make_token, make_csrf, verify_token, get_client_ip, _login_page)

limiter = Limiter(key_func=get_client_ip)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class _SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self'; "
            "script-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if SECURE_COOKIES:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(AuthMiddleware)
app.add_middleware(_SecurityHeaders)

STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _require_client():
    if _client is None:
        raise HTTPException(503, "SmartHQ credentials not configured — edit .env and restart")
    return _client


# ── Auth routes ───────────────────────────────────────────────────

@app.get("/login", include_in_schema=False)
async def login_page():
    return HTMLResponse(_login_page())


def _ct_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(a.encode()).digest(),
        hashlib.sha256(b.encode()).digest(),
    )


@app.post("/login", include_in_schema=False)
@limiter.limit("10/minute")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not (_ct_eq(username, AUTH_USERNAME) and _ct_eq(password, AUTH_PASSWORD)):
        return HTMLResponse(_login_page("Incorrect username or password."), status_code=401)

    next_url = request.query_params.get("next", "/")
    if (not next_url.startswith("/")) or next_url.startswith("//") or next_url.startswith("/\\"):
        next_url = "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        next_url = "/"

    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        COOKIE_NAME, make_token(AUTH_USERNAME),
        max_age=COOKIE_DAYS * 86400,
        httponly=True, samesite="lax", secure=SECURE_COOKIES,
    )
    response.set_cookie(
        CSRF_COOKIE, make_csrf(),
        max_age=COOKIE_DAYS * 86400,
        httponly=False, samesite="lax", secure=SECURE_COOKIES,
    )
    return response


@app.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE)
    return response


# ── UI ────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")


# ── API ───────────────────────────────────────────────────────────

@app.get("/api/devices")
@limiter.limit("30/minute")
async def list_devices(request: Request):
    return _require_client().list_devices()


@app.get("/api/status")
@limiter.limit("120/minute")
async def get_status(request: Request):
    c = _require_client()
    try:
        return c.get_status()
    except RuntimeError:
        log.exception("get_status failed")
        raise HTTPException(503, "Device temporarily unavailable")


class ControlRequest(BaseModel):
    power: Literal["on", "off"] | None = None
    mode:  Literal["cool", "eco", "fan"] | None = None
    temp:  int | None = Field(default=None, ge=60, le=86)
    fan:   Literal["auto", "low", "medium", "high"] | None = None


@app.post("/api/control")
@limiter.limit("30/minute")
async def control(request: Request, req: ControlRequest):
    c = _require_client()
    if not any([req.power, req.mode, req.temp, req.fan]):
        raise HTTPException(400, "Provide at least one field to change")
    try:
        return await c.control(req.power, req.mode, req.temp, req.fan)
    except RuntimeError:
        log.exception("control failed")
        raise HTTPException(503, "Device temporarily unavailable")


# ── Sleep timer ───────────────────────────────────────────────

async def _run_timer(seconds: int):
    global _timer_task, _timer_ends_at
    try:
        await asyncio.sleep(seconds)
        if _client:
            await _client.control("off", None, None, None)
            log.info("Sleep timer fired — AC turned off")
    except asyncio.CancelledError:
        pass
    finally:
        _timer_task = None
        _timer_ends_at = None


def _timer_status() -> dict:
    if _timer_task is None or _timer_task.done():
        return {"active": False, "remaining_seconds": None, "ends_at": None}
    remaining = max(0, (_timer_ends_at - datetime.now(timezone.utc)).total_seconds())
    return {
        "active": True,
        "remaining_seconds": int(remaining),
        "ends_at": _timer_ends_at.isoformat(),
    }


class TimerRequest(BaseModel):
    minutes: int | None = Field(default=None, ge=1, le=1440)


@app.get("/api/timer")
@limiter.limit("120/minute")
async def get_timer(request: Request):
    return _timer_status()


@app.post("/api/timer")
@limiter.limit("30/minute")
async def set_timer(request: Request, req: TimerRequest):
    global _timer_task, _timer_ends_at
    if _timer_task and not _timer_task.done():
        _timer_task.cancel()
    _timer_task = None
    _timer_ends_at = None

    if req.minutes is not None:
        _require_client()
        _timer_ends_at = datetime.now(timezone.utc) + timedelta(minutes=req.minutes)
        _timer_task = asyncio.create_task(_run_timer(req.minutes * 60))

    return _timer_status()
