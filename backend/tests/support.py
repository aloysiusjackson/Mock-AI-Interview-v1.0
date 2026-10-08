"""
support.py — shared harness for the backend test suite.

Two things matter here:

1.  Importing `app` runs `init_db()` immediately, so the database path and every
    credential-bearing environment variable are set up *before* the import.
2.  The suite must never email a real address or call a live model. SMTP is
    stubbed out and the suite refuses to start if a Gemini key is present.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
import warnings
from urllib.parse import parse_qs, urlparse

# Flask's test client leaves the static-file wrapper open, which floods the
# output with ResourceWarnings that have nothing to do with this codebase.
warnings.filterwarnings("ignore", category=ResourceWarning)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ── Isolate the environment BEFORE app.py is imported ───────────────────────
# app.py calls load_dotenv(), which never overwrites variables that already
# exist, so assigning empty strings here guarantees the tests stay offline even
# if the developer's real .env holds live credentials.
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["SMTP_EMAIL"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["CONTEST_ALLOWED_ORIGINS"] = ""
os.environ.pop("ALLOWED_ORIGINS", None)

# The suite asserts the local-development behaviour of the auth flows (inline
# verification/reset links are handed back when SMTP is off) and that the demo
# logins exist, so pin both switches on regardless of which engine is tested.
os.environ["DEV_AUTH_LINKS"] = "1"
os.environ["SEED_DEMO_ACCOUNTS"] = "1"

# Engine selection for the suite. Empty (the default) keeps the original SQLite
# run; TEST_DATABASE_URL runs the *same* tests against PostgreSQL — that is how
# the Supabase migration is verified. Setting it explicitly also stops a real
# DATABASE_URL in a developer's .env from being picked up by load_dotenv().
PG_TEST_URL = (os.environ.get("TEST_DATABASE_URL") or "").strip()
os.environ["DATABASE_URL"] = PG_TEST_URL

_TMP_DIR = tempfile.mkdtemp(prefix="mock-interview-tests-")
TEST_DB_PATH = os.path.join(_TMP_DIR, "test.db")

import database  # noqa: E402  — must come after the environment setup above

if database.using_postgres():
    # PostgreSQL run: start from an empty schema so the tests below see the same
    # clean slate the SQLite run gets from its temporary file.
    import pg_schema  # noqa: E402

    _conn = database.get_db_connection()
    try:
        pg_schema.create_schema(_conn)
        pg_schema.truncate_tables(_conn)
    finally:
        _conn.close()
else:
    # Redirect storage to the throwaway file. get_db_connection() reads the
    # module global at call time, so this also covers DB access from inside
    # app.py.
    database.DB_PATH = TEST_DB_PATH

import app as app_module  # noqa: E402
import ai_engine  # noqa: E402
import security  # noqa: E402

# ── Keep the suite fast ────────────────────────────────────────────────────
# 600k PBKDF2 rounds are correct in production but would add minutes across a
# few hundred assertions. Drop to a cheap-but-real KDF for the run; the suite
# separately asserts the shipped default is still strong.
if security.PBKDF2_METHOD != "pbkdf2:sha256:600000":
    raise RuntimeError(
        "security.PBKDF2_METHOD changed — update this test harness and re-check "
        "the hashing-strength test before lowering the cost."
    )
security.PBKDF2_METHOD = "pbkdf2:sha256:1000"

# ── Safety nets ────────────────────────────────────────────────────────────
if ai_engine.GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is set in this environment — refusing to run the suite "
        "against a live model and charge the account."
    )

# Recorders replace the real senders, so nothing can leave the machine.
sent_emails: list[dict] = []


def _record_verification(email, token, name):
    sent_emails.append({"kind": "verification", "email": email, "token": token, "name": name})
    return True


def _record_reset(email, token, name):
    sent_emails.append({"kind": "reset", "email": email, "token": token, "name": name})
    return True


app_module.send_verification_email = _record_verification
app_module.send_reset_email = _record_reset
# Reporting "not configured" makes signup/reset return the link inline, which is
# exactly the local-development behaviour the flow tests want to assert on.
app_module.smtp_configured = lambda: False

flask_app = app_module.app
flask_app.config.update(TESTING=True)


def token_from_link(link: str) -> str:
    """Pull the ?token= value out of a verification or reset URL."""
    return parse_qs(urlparse(link).query)["token"][0]


def fetch_text(client, path: str, **kwargs):
    """GET a static file and release its handle.

    Werkzeug serves static files as a passthrough stream; the test client never
    closes it, so reading the body and closing it explicitly keeps the run's
    output free of unrelated ResourceWarnings.
    Returns (response, body_text).
    """
    response = client.get(path, **kwargs)
    text = response.get_data(as_text=True)
    response.close()
    return response, text


def query_one(sql: str, params: tuple = ()):
    """Run a single SELECT against the test database and return one row."""
    conn = database.get_db_connection()
    try:
        cursor = conn.execute(sql, params)
        return cursor.fetchone()
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    """Run a write against the test database, returning lastrowid."""
    conn = database.get_db_connection()
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


class ApiTestCase(unittest.TestCase):
    """Base class giving each test a fresh client and a clean rate limiter."""

    def setUp(self):
        # The limiter is process-global, so reset it or one test's failed logins
        # would 429 the next test.
        security.LIMITER.reset()
        sent_emails.clear()
        self.client = flask_app.test_client()

    def new_client(self):
        """A second, independent browser (separate cookie jar)."""
        return flask_app.test_client()

    # ── Account helpers ────────────────────────────────────────────────────
    def unique_email(self, prefix: str = "user") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"

    def signup(self, *, name="Test User", email=None, password="Str0ngPass1", client=None):
        """Register an account. Returns (response, email, password)."""
        email = email or self.unique_email()
        client = client or self.client
        response = client.post(
            "/api/auth/signup",
            json={"name": name, "email": email, "password": password},
        )
        return response, email, password

    def signup_verified(self, *, client=None, **kwargs):
        """Register and complete email verification. Returns (email, password)."""
        client = client or self.client
        response, email, password = self.signup(client=client, **kwargs)
        self.assertEqual(response.status_code, 200, response.get_json())
        link = response.get_json()["verification_link"]
        verified = client.get(f"/api/auth/verify-email?token={token_from_link(link)}")
        self.assertEqual(verified.status_code, 200)
        return email, password

    def login(self, email, password, client=None):
        client = client or self.client
        return client.post("/api/auth/login", json={"email": email, "password": password})

    def signed_in_client(self, **kwargs):
        """A client with an authenticated session. Returns (client, email)."""
        client = flask_app.test_client()
        email, password = self.signup_verified(client=client, **kwargs)
        response = self.login(email, password, client=client)
        self.assertEqual(response.status_code, 200, response.get_json())
        return client, email
