"""
Auth middleware — transparent on LAN, login required from outside.

LAN detection uses Python's ipaddress.is_private which covers:
  10.x.x.x, 172.16-31.x.x, 192.168.x.x, 127.x.x.x, ::1, etc.
"""
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

log = logging.getLogger(__name__)

COOKIE_NAME = "ac_session"
COOKIE_DAYS = 30

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "admin")
_SECRET = os.getenv("SESSION_SECRET", "")

if not _SECRET:
    _SECRET = secrets.token_hex(32)
    log.warning(
        "SESSION_SECRET not set in .env — sessions will be invalidated on restart. "
        "Add SESSION_SECRET=%s to .env to make them persistent.", _SECRET
    )


# ── Helpers ────────────────────────────────────────────────────────

def is_local(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _sign(value: str) -> str:
    return hmac.new(_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    ts = str(int(time.time()))
    return f"{ts}.{_sign(ts)}"


def verify_token(token: str) -> bool:
    try:
        ts, sig = token.split(".", 1)
        return hmac.compare_digest(_sign(ts), sig)
    except Exception:
        return False


# ── Login page HTML ────────────────────────────────────────────────

def _login_page(error: str = "") -> str:
    err_html = (
        f'<div class="error">{error}</div>' if error else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AC Control — Login</title>
<style>
  :root {{
    --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a;
    --accent: #0ea5e9; --text: #e2e8f0; --muted: #64748b;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    min-height: 100dvh;
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
  }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 16px; padding: 36px 32px; width: 100%; max-width: 360px;
  }}
  h1 {{ font-size: 1.2rem; font-weight: 700; margin-bottom: 6px; }}
  p {{ color: var(--muted); font-size: .85rem; margin-bottom: 28px; }}
  label {{ display: block; font-size: .75rem; letter-spacing: .08em;
           text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }}
  input {{
    width: 100%; padding: 10px 14px; border-radius: 10px;
    border: 1px solid var(--border); background: var(--bg);
    color: var(--text); font-size: .95rem; margin-bottom: 16px; outline: none;
  }}
  input:focus {{ border-color: var(--accent); }}
  button {{
    width: 100%; padding: 12px; border-radius: 10px; border: none;
    background: var(--accent); color: #fff; font-size: .95rem;
    font-weight: 600; cursor: pointer; transition: opacity .15s;
  }}
  button:hover {{ opacity: .85; }}
  .error {{
    background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.3);
    border-radius: 10px; padding: 10px 14px; color: #fca5a5;
    font-size: .82rem; margin-bottom: 16px;
  }}
</style>
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
        # Always allow login/logout routes
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Trusted if request comes from a private/LAN IP
        client_ip = request.client.host if request.client else "127.0.0.1"
        if is_local(client_ip):
            return await call_next(request)

        # External: require a valid session cookie
        token = request.cookies.get(COOKIE_NAME)
        if token and verify_token(token):
            return await call_next(request)

        # No valid session — redirect to login
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
