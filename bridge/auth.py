"""
Auth middleware — login required for every request (no LAN bypass).

A valid `ac_session` cookie binds the session to the configured username via
HMAC. All unsafe methods (POST/PUT/PATCH/DELETE) also require a matching
`X-CSRF-Token` header (double-submit against the `ac_csrf` cookie) plus an
Origin/Referer host check.

TRUSTED_PROXY:  set to your reverse-proxy IP (e.g. "127.0.0.1") when running
                behind Nginx/Caddy so X-Forwarded-For is used for the real
                client IP (slowapi rate-limit key only — no longer affects auth).
SECURE_COOKIES: set to "true" when serving over HTTPS.
"""
import hashlib
import hmac
import html
import logging
import os
import secrets
import time
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

log = logging.getLogger(__name__)

COOKIE_NAME = "ac_session"
CSRF_COOKIE = "ac_csrf"
COOKIE_DAYS = 30
_MAX_TOKEN_AGE = COOKIE_DAYS * 86400

TRUSTED_PROXY  = os.getenv("TRUSTED_PROXY", "").strip()
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "").lower() in ("1", "true", "yes")

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "admin")
_SECRET = os.getenv("SESSION_SECRET", "")

if not _SECRET:
    _SECRET = secrets.token_hex(32)
    log.warning(
        "SESSION_SECRET not set in .env — sessions will be invalidated on restart. "
        "Add SESSION_SECRET=%s to .env to make them persistent.", _SECRET
    )

_WEAK = {"", "admin", "changeme", "password", "root"}
if AUTH_USERNAME.lower() in _WEAK or AUTH_PASSWORD in _WEAK or len(AUTH_PASSWORD) < 8:
    log.warning(
        "AUTH_USERNAME/AUTH_PASSWORD is weak or default — update .env before "
        "exposing this server beyond localhost."
    )


# ── Helpers ────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    """Return the real client IP, honouring X-Forwarded-For when TRUSTED_PROXY is set.
    Used as slowapi's rate-limit key; no longer affects auth decisions."""
    ip = request.client.host if request.client else "127.0.0.1"
    if TRUSTED_PROXY and ip == TRUSTED_PROXY:
        xff = request.headers.get("X-Forwarded-For", "")
        ip = xff.split(",")[0].strip() or ip
    return ip


def _sign(value: str) -> str:
    return hmac.new(_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_token(username: str) -> str:
    ts = str(int(time.time()))
    payload = f"{username}.{ts}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """Return username on success, else None."""
    try:
        username, ts, sig = token.split(".", 2)
    except ValueError:
        return None
    payload = f"{username}.{ts}"
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    if not hmac.compare_digest(username, AUTH_USERNAME):
        return None
    try:
        if (time.time() - int(ts)) >= _MAX_TOKEN_AGE:
            return None
    except ValueError:
        return None
    return username


def make_csrf() -> str:
    return secrets.token_urlsafe(32)


# ── Login page HTML ────────────────────────────────────────────────

def _login_page(error: str = "") -> str:
    err_html = (
        f'<div class="error">{html.escape(error)}</div>' if error else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AC Control — Login</title>
<link rel="stylesheet" href="/static/login.css">
</head>
<body>
<div class="card">
  <h1>AC Control</h1>
  <p>Sign in to control your AC remotely.</p>
  {err_html}
  <form method="post" action="/login">
    <label>Username</label>
    <input type="text" name="username" autocomplete="username" required autofocus>
    <label>Password</label>
    <input type="password" name="password" autocomplete="current-password" required>
    <button type="submit">Sign in</button>
  </form>
</div>
</body>
</html>"""


# ── Middleware ─────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/login", "/logout", "/favicon.ico"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        user = verify_token(token) if token else None
        if not user:
            if path.startswith("/api/"):
                return Response(status_code=401)
            return RedirectResponse(f"/login?next={path}", status_code=303)

        # CSRF: double-submit cookie + Origin/Referer host check for unsafe methods
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
            header_csrf = request.headers.get("X-CSRF-Token", "")
            if not cookie_csrf or not hmac.compare_digest(cookie_csrf, header_csrf):
                return Response("CSRF check failed", status_code=403)
            origin = request.headers.get("origin") or request.headers.get("referer", "")
            if origin:
                netloc = urlparse(origin).netloc
                host_hdr = request.headers.get("host", "")
                if netloc and netloc != host_hdr:
                    return Response("Bad origin", status_code=403)

        response = await call_next(request)
        # Issue a CSRF cookie if missing (first authenticated GET after login)
        if not request.cookies.get(CSRF_COOKIE):
            response.set_cookie(
                CSRF_COOKIE, make_csrf(),
                max_age=COOKIE_DAYS * 86400,
                httponly=False, samesite="lax", secure=SECURE_COOKIES,
            )
        return response
