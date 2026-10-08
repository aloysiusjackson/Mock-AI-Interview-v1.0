"""
security.py — authentication & transport hardening for the Mock AI Interview API.

This module owns everything that keeps the API *safe* (as opposed to making it
work): password hashing and policy, brute-force rate limiting, security response
headers, cross-site request checks, and small helpers for parsing JSON and
reporting errors without leaking internals.

Nothing here imports `database`, so the module stays trivially unit-testable.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import jsonify, redirect, request, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

# ══════════════════════════════════════════════════════════════════════════
#  1. PASSWORD HASHING
# ══════════════════════════════════════════════════════════════════════════

# PBKDF2-HMAC-SHA256 with 600k iterations. Werkzeug ships this so the project
# gains real key stretching without adding a dependency. The old code used a
# single unsalted-round sha256(password + salt), which is GPU-crackable at
# billions of guesses/second.
PBKDF2_METHOD = "pbkdf2:sha256:600000"


def is_modern_hash(stored: str) -> bool:
    """True when the stored value already uses a werkzeug KDF format."""
    return bool(stored) and (stored.startswith("pbkdf2:") or stored.startswith("scrypt:"))


def hash_password(password: str) -> str:
    """Hash a password with a salted, stretched KDF."""
    return generate_password_hash(password, method=PBKDF2_METHOD)


def verify_password(password: str, stored: str) -> bool:
    """Constant-time password check that also understands legacy hashes.

    Returns False (never raises) for malformed or empty stored values so a
    corrupt row degrades to "login failed" instead of a 500.
    """
    if not password or not stored or not isinstance(stored, str):
        return False

    try:
        if is_modern_hash(stored):
            return check_password_hash(stored, password)

        # Legacy format written by the original code: "<hex salt>:<sha256 hex>"
        salt, sep, digest = stored.partition(":")
        if not sep or not digest:
            return False
        candidate = hashlib.sha256((password + salt).encode("utf-8")).hexdigest()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


def needs_rehash(stored: str) -> bool:
    """True when a verified hash is still on the weak legacy scheme.

    Callers use this to transparently upgrade a user's hash at login without
    forcing anyone to reset their password.
    """
    return bool(stored) and not is_modern_hash(stored)


def dummy_verify(password: str) -> None:
    """Spend hash-comparable time when no account matches.

    Without this, a request for an unknown address returns far faster than one
    for a real address, which leaks which emails are registered.
    """
    verify_password(password, f"{PBKDF2_METHOD}$dummy$" + "0" * 64)


# ══════════════════════════════════════════════════════════════════════════
#  2. PASSWORD POLICY
# ══════════════════════════════════════════════════════════════════════════

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128  # caps KDF work per request (DoS guard)

# A short blocklist of the passwords that dominate credential-stuffing lists.
# Deliberately small and dependency-free; swap for a full breach corpus if the
# project ever needs one.
_COMMON_PASSWORDS = frozenset({
    "password", "password1", "password12", "password123", "password1234",
    "passw0rd", "p@ssw0rd", "12345678", "123456789", "1234567890",
    "qwerty123", "qwertyui", "iloveyou", "admin123", "administrator",
    "letmein1", "welcome1", "welcome123", "abc12345", "football1",
    "monkey123", "dragon123", "trustno1", "sunshine1", "princess1",
    "interview", "interview1", "mockinterview", "changeme", "secret123",
})


def validate_password(password, *, email: str = "", name: str = "") -> str | None:
    """Return an error message, or None when the password is acceptable.

    Rules: 8–128 chars, at least one letter and one digit, not a well-known
    password, and not built out of the account's own name or email.
    """
    if not isinstance(password, str):
        return "Password must be text."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."

    lowered = password.lower()

    if lowered in _COMMON_PASSWORDS:
        return "That password is too common. Please choose a less predictable one."

    missing = []
    if not re.search(r"[A-Za-z]", password):
        missing.append("a letter")
    if not re.search(r"\d", password):
        missing.append("a number")
    if missing:
        return "Password must include " + " and ".join(missing) + "."

    # Reject passwords that merely wrap the user's own identity.
    for value in (email, name):
        token = (value or "").split("@")[0].strip().lower()
        if len(token) >= 4 and token in lowered:
            return "Password must not contain your name or email address."

    return None


# ══════════════════════════════════════════════════════════════════════════
#  3. RATE LIMITING
# ══════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """Thread-safe sliding-window limiter keyed by an arbitrary string.

    In-process and therefore per-worker. That is the right trade-off for a
    single-container deployment; move to Redis if the API is ever scaled out.
    """

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = int(window_seconds - (now - bucket[0])) + 1
                return False, max(retry_after, 1)
            bucket.append(now)
            return True, 0

    def reset(self, prefix: str | None = None) -> None:
        """Clear counters (all of them, or every key starting with `prefix`)."""
        with self._lock:
            if prefix is None:
                self._hits.clear()
                return
            for key in [k for k in self._hits if k.startswith(prefix)]:
                del self._hits[key]


LIMITER = RateLimiter()


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment switch ("1", "true", "yes")."""
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def trust_proxy() -> bool:
    """True when the app runs behind exactly one reverse proxy (Render, nginx)."""
    return env_flag("TRUST_PROXY")


def force_https() -> bool:
    """True when plain-HTTP requests must be redirected to HTTPS."""
    return env_flag("FORCE_HTTPS")


def client_ip() -> str:
    """The client address used to key rate limits.

    ``X-Forwarded-For`` is only consulted when TRUST_PROXY is on, and even then
    it is ProxyFix — not this function — that reads it: ProxyFix rewrites
    ``request.remote_addr`` to the address the proxy actually observed (the
    rightmost entry). Reading the header directly would let a caller choose its
    own rate-limit bucket by sending a forged first hop.
    """
    return request.remote_addr or "unknown"


def rate_limit(bucket: str, limit: int, window_seconds: int, *, by: str = "ip"):
    """Decorator limiting how often a view may run.

    `by="ip"` keys on the client address. Pass `by="email"` to additionally
    throttle a specific account so one attacker cannot spray many addresses
    from a single IP, nor hammer one account from many IPs.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            keys = [f"{bucket}:ip:{client_ip()}"]
            if by == "email":
                payload = safe_json()
                identity = str(payload.get("email", "")).strip().lower()
                if identity:
                    keys.append(f"{bucket}:id:{identity}")

            for key in keys:
                allowed, retry_after = LIMITER.check(key, limit, window_seconds)
                if not allowed:
                    response = jsonify({
                        "error": "Too many attempts. Please wait a moment and try again.",
                        "retry_after": retry_after,
                    })
                    response.status_code = 429
                    response.headers["Retry-After"] = str(retry_after)
                    return response
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ══════════════════════════════════════════════════════════════════════════
#  4. JSON / ERROR HELPERS
# ══════════════════════════════════════════════════════════════════════════

def safe_json() -> dict:
    """Always return a dict for a JSON body.

    `request.get_json()` returns None for a missing or malformed body, so the
    old `data.get(...)` calls raised AttributeError and surfaced as 500s.
    """
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def api_error(message: str, status: int = 400, **extra):
    """JSON error response. Internal exception text is never passed through."""
    payload = {"error": message}
    payload.update(extra)
    return jsonify(payload), status


def log_exception(context: str, exc: Exception) -> None:
    """Log the real reason server-side while the client gets a generic message."""
    print(f"[ERROR] {context}: {type(exc).__name__}: {exc}")


# ══════════════════════════════════════════════════════════════════════════
#  5. SESSION IDENTITY
# ══════════════════════════════════════════════════════════════════════════

def login_user(user_id: int) -> None:
    """Establish the server-side identity for this browser.

    Identity lives in a signed, HttpOnly cookie session. Endpoints read it from
    here — never from a client-supplied `user_id` — which is what closes the
    "read or edit any account by guessing an id" hole.
    """
    session.clear()
    session["user_id"] = int(user_id)
    session.permanent = True


def logout_user() -> None:
    session.clear()


def current_user_id() -> int | None:
    value = session.get("user_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def require_auth(fn):
    """Reject the request unless a session identity is present."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user_id() is None:
            return api_error("Authentication required.", 401)
        return fn(*args, **kwargs)
    return wrapper


# ══════════════════════════════════════════════════════════════════════════
#  6. SECRET KEY
# ══════════════════════════════════════════════════════════════════════════

def resolve_secret_key(app_root: str) -> str:
    """Return a stable secret key.

    Previously the key was regenerated on every boot, which invalidated every
    session each restart. Prefer the environment; otherwise persist a generated
    key next to the app so restarts keep sessions (and stay single-worker safe).
    """
    from_env = (os.environ.get("SECRET_KEY") or "").strip()
    if from_env:
        return from_env

    key_path = os.path.join(app_root, ".flask_secret")
    try:
        if os.path.exists(key_path):
            with open(key_path, "r", encoding="utf-8") as handle:
                existing = handle.read().strip()
            if existing:
                return existing
        generated = secrets.token_hex(32)
        with open(key_path, "w", encoding="utf-8") as handle:
            handle.write(generated)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass  # Windows / restricted filesystems
        return generated
    except OSError:
        # Read-only filesystem: fall back to an ephemeral key rather than crash.
        return secrets.token_hex(32)


# ══════════════════════════════════════════════════════════════════════════
#  7. CROSS-SITE REQUEST PROTECTION
# ══════════════════════════════════════════════════════════════════════════

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def allowed_origins() -> set[str]:
    """Same-origin by default, plus anything in ALLOWED_ORIGINS."""
    origins = {request.host_url.rstrip("/")}
    extra = os.environ.get("ALLOWED_ORIGINS", "")
    for item in extra.split(","):
        item = item.strip().rstrip("/")
        if item:
            origins.add(item)
    return origins


def same_origin_request() -> bool:
    """Defence-in-depth CSRF check for state-changing requests.

    Browsers always attach `Origin` to cross-site (and same-origin) non-GET
    requests, so comparing it against our own host blocks classic CSRF. The
    session cookie is also SameSite=Lax, so this is a second layer rather than
    the only one.
    """
    origin = request.headers.get("Origin")
    if origin and origin != "null":
        return origin.rstrip("/") in allowed_origins()

    referer = request.headers.get("Referer")
    if referer:
        parsed = urlparse(referer)
        return f"{parsed.scheme}://{parsed.netloc}" in allowed_origins()

    # No Origin/Referer: a non-browser client (curl, tests, server-to-server).
    return True


def csrf_protect(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method not in SAFE_METHODS and not same_origin_request():
            return api_error("Cross-site request blocked.", 403)
        return fn(*args, **kwargs)
    return wrapper


# ══════════════════════════════════════════════════════════════════════════
#  8. SECURITY HEADERS
# ══════════════════════════════════════════════════════════════════════════

def _content_security_policy() -> str:
    """A CSP that is strict about the dangerous bits and realistic about the rest.

    The pages rely on inline <style>/<script> blocks and inline event handlers,
    so 'unsafe-inline' is required for scripts and styles. The value still buys
    real protection: object-src 'none', base-uri, form-action and
    frame-ancestors (clickjacking) are all locked down.
    """
    return "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://accounts.google.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: https:",
        "connect-src 'self' https://accounts.google.com https://mock-ai-interview-backend.onrender.com",
        "frame-src https://accounts.google.com",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def security_headers(secure_request: bool = False) -> dict[str, str]:
    """Headers applied to every response."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), camera=(self), microphone=(self)",
        "Content-Security-Policy": _content_security_policy(),
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    # HSTS only makes sense (and is only honoured) over HTTPS.
    if secure_request:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


# ══════════════════════════════════════════════════════════════════════════
#  9. ACTIVITY LOGGING

def log_activity(
    user_id: int | None,
    activity_type: str,
    description: str = "",
    status: str = "success",
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: str | None = None,
) -> None:
    """Write an immutable activity record for admin auditing.

    Designed to be called from anywhere in the app that has a Flask request
    context.  Silently swallows errors so logging can never be the cause of a
    500 — a failing audit trail is better than an unavailable application.
    """
    try:
        from flask import request
        from database import get_db_connection

        conn = get_db_connection()
        try:
            conn.execute(
                """INSERT INTO activity_logs
                   (user_id, activity_type, description, status, ip_address, user_agent, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    activity_type,
                    description,
                    status,
                    ip_address or (request.remote_addr if request else None),
                    (request.headers.get("User-Agent") or "")[:512] if request else None,
                    details,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[WARN] activity_log failed: {exc}")


def require_admin(fn):
    """Reject the request unless the signed-in user is an admin.

    Backend gate for every `/admin/*` route.  Frontend checks are only a
    convenience — this decorator is what actually protects the panel.
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        from security import current_user_id, api_error
        uid = current_user_id()
        if uid is None:
            return api_error("Authentication required.", 401)
        from database import get_db_connection
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT role FROM users WHERE id = ?", (uid,)
            ).fetchone()
        finally:
            conn.close()
        if not row or row["role"] != "admin":
            return api_error("Admin access required.", 403)
        return fn(*args, **kwargs)
    return wrapper


# ══════════════════════════════════════════════════════════════════════════
#  10. APP WIRING
# ══════════════════════════════════════════════════════════════════════════

def configure_app(app, secret_key: str) -> None:
    """Apply session/cookie/size settings and register the global hooks."""
    app.secret_key = secret_key

    # Behind one reverse proxy (Render terminates TLS), the original scheme and
    # client address arrive in X-Forwarded-* headers. Without this, Flask sees
    # plain HTTP from the proxy's own IP, so HSTS is never sent, request.host_url
    # (used in verification emails) can be wrong, and every visitor shares one
    # rate-limit bucket. Off by default so a direct-to-process deployment cannot
    # have those headers spoofed.
    if trust_proxy():
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.config.update(
        # Cookie session hardening
        SESSION_COOKIE_HTTPONLY=True,     # JS cannot read the session cookie
        SESSION_COOKIE_SAMESITE="Lax",    # blocks cross-site POSTs
        SESSION_COOKIE_SECURE=env_flag("COOKIE_SECURE"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        # Cap request bodies so a huge upload/JSON blob cannot exhaust memory
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)),
    )

    @app.before_request
    def _redirect_to_https():
        # Host-side TLS termination usually already redirects; this covers hosts
        # that do not. 308 preserves the method so a POST is not silently turned
        # into a GET.
        if not force_https() or request.is_secure:
            return None
        target = request.url.replace("http://", "https://", 1)
        return redirect(target, code=301 if request.method in SAFE_METHODS else 308)

    @app.before_request
    def _block_cross_site_writes():
        if request.method in SAFE_METHODS:
            return None
        if not request.path.startswith("/api/"):
            return None
        if not same_origin_request():
            return api_error("Cross-site request blocked.", 403)
        return None

    @app.after_request
    def _apply_security_headers(response):
        for header, value in security_headers(secure_request=request.is_secure).items():
            response.headers.setdefault(header, value)
        return response

    @app.errorhandler(413)
    def _payload_too_large(_error):
        return api_error("Payload too large.", 413)

    @app.errorhandler(500)
    def _internal_error(error):
        # Keep the traceback in the server log, never in the response.
        log_exception("unhandled", error if isinstance(error, Exception) else Exception(str(error)))
        return api_error("Something went wrong on our end.", 500)
