"""Tests for the switches that keep a *public* deployment safe.

These behaviours default to the dangerous-but-convenient setting for local SQLite
development and turn themselves off as soon as ``DATABASE_URL`` points at
production PostgreSQL. The rest of the suite pins them on (see support.py), so
these tests flip them explicitly and restore them afterwards.

1. ``DEV_AUTH_LINKS`` — whether an API response may contain a verification or
   password-reset link. On a public site that response *is* the token, which
   would let anyone reset any account's password by simply asking for it.
2. ``SEED_DEMO_ACCOUNTS`` — whether the published demo logins
   (admin@demo.com / Demo@1234) are (re)created by this process.
3. ``TRUST_PROXY`` / ``FORCE_HTTPS`` — HTTPS behind a reverse proxy: HSTS,
   the real client address for rate limiting, and the HTTP→HTTPS redirect.
4. The Vercel entrypoint serving the *same* hardened app instead of a second,
   unauthenticated copy of the API.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import unittest

from flask import Flask

import security

# support must be imported FIRST: it redirects the database to a temporary file
# before app.py is imported (importing app runs init_db(), which would otherwise
# initialise the real backend/mock_interview.db).
from support import BACKEND_DIR, ApiTestCase, execute, query_one

import app as app_module  # noqa: E402
import database  # noqa: E402

POSTGRES_SHAPED_DSN = "postgresql://user:secret@example.supabase.com:5432/postgres"


class SwitchTestCase(ApiTestCase):
    """Gives each test a safe way to change an environment switch."""

    def set_switch(self, name: str, value: str) -> None:
        previous = os.environ.get(name)
        os.environ[name] = value

        def restore():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

        self.addCleanup(restore)


class DevAuthLinkTests(SwitchTestCase):
    def test_verification_link_is_hidden_when_disabled(self):
        """Signup must not hand back the verification link in production."""
        self.set_switch("DEV_AUTH_LINKS", "0")
        self.assertFalse(app_module.dev_auth_links_enabled())

        response, email, _password = self.signup()
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertFalse(payload["email_sent"])              # SMTP is off in tests
        self.assertNotIn("verification_link", payload)       # ...and no link either

        # The account still exists and is still unverified, so nothing else broke.
        row = query_one("SELECT is_verified, verification_token FROM users WHERE email = ?", (email,))
        self.assertIsNotNone(row)
        self.assertEqual(row["is_verified"], 0)
        self.assertTrue(row["verification_token"], "a token must still be generated")

    def test_reset_link_is_hidden_when_disabled_and_shown_when_enabled(self):
        """Forgot-password must not leak the reset token on a public deployment."""
        email, _password = self.signup_verified()

        self.set_switch("DEV_AUTH_LINKS", "0")
        response = self.client.post("/api/auth/forgot-password", json={"email": email})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("reset_link", response.get_json())

        # Local development keeps working exactly as before.
        self.set_switch("DEV_AUTH_LINKS", "1")
        response = self.client.post("/api/auth/forgot-password", json={"email": email})
        self.assertEqual(response.status_code, 200)
        self.assertIn("reset_link", response.get_json())

    def test_login_does_not_return_a_verification_link_when_disabled(self):
        self.set_switch("DEV_AUTH_LINKS", "0")
        response, email, password = self.signup()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("verification_link", response.get_json())

        login = self.login(email, password)
        self.assertEqual(login.status_code, 403)
        payload = login.get_json()
        self.assertTrue(payload["needs_verification"])
        self.assertNotIn("verification_link", payload)


class AuthTokenLoggingTests(SwitchTestCase):
    """A verification or reset token must not reach a production log.

    The token is a credential: reading the log would be enough to verify
    somebody else's address or reset their password. Local development still
    prints it, because there DEV_AUTH_LINKS hands the same token back to the
    caller anyway.
    """

    TOKEN = "tok_6d0f1c9a-beef-4c1e-9a77-DEADBEEF"

    def _logged(self, kind: str) -> str:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            app_module.note_unsent_auth_token(kind, "someone@example.com", self.TOKEN)
        return buffer.getvalue()

    def test_token_is_withheld_once_links_are_disabled(self):
        self.set_switch("DEV_AUTH_LINKS", "0")
        for kind in ("verification", "reset"):
            with self.subTest(kind=kind):
                logged = self._logged(kind)
                self.assertNotIn(self.TOKEN, logged)
                self.assertIn("withheld", logged)

    def test_local_development_still_prints_it(self):
        self.set_switch("DEV_AUTH_LINKS", "1")
        self.assertIn(self.TOKEN, self._logged("verification"))

    def test_senders_never_print_a_verification_or_reset_hash(self):
        """No print in app.py may interpolate a password hash."""
        with open(os.path.join(BACKEND_DIR, "app.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("password_hash}", source)
        self.assertNotIn("{password}", source)


class DemoSeedingGateTests(SwitchTestCase):
    def test_explicit_flags_win(self):
        self.set_switch("SEED_DEMO_ACCOUNTS", "0")
        self.assertFalse(database.demo_accounts_enabled())
        self.set_switch("SEED_DEMO_ACCOUNTS", "1")
        self.assertTrue(database.demo_accounts_enabled())

    def test_default_follows_the_engine(self):
        """Enabled for local SQLite, disabled once DATABASE_URL is set.

        Written to hold on either engine so the same module can power both the
        SQLite run and the PostgreSQL run (TEST_DATABASE_URL sets DATABASE_URL).
        """
        self.set_switch("SEED_DEMO_ACCOUNTS", "")
        self.set_switch("DEV_AUTH_LINKS", "")
        on_engine_with_dsn = database.using_postgres()
        self.assertEqual(database.demo_accounts_enabled(), not on_engine_with_dsn)
        self.assertEqual(app_module.dev_auth_links_enabled(), not on_engine_with_dsn)

        # A production DSN flips the default without needing a real server: the
        # decision only inspects the URL, it never connects.
        self.set_switch("DATABASE_URL", POSTGRES_SHAPED_DSN)
        self.assertTrue(database.using_postgres())
        self.assertFalse(database.demo_accounts_enabled())
        self.assertFalse(app_module.dev_auth_links_enabled())

    def test_seeding_is_skipped_when_disabled(self):
        """With the gate closed the demo roster is not touched at all."""
        self.set_switch("SEED_DEMO_ACCOUNTS", "0")
        execute("DELETE FROM users WHERE email = ?", ("admin@demo.com",))
        self.assertIsNone(query_one("SELECT id FROM users WHERE email = ?", ("admin@demo.com",)))

        connection = database.get_db_connection()
        try:
            database.seed_demo_accounts(connection.cursor())
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(query_one("SELECT id FROM users WHERE email = ?", ("admin@demo.com",)))

        # Put the account back so the rest of the suite is unaffected, and prove
        # the same call does restore it when the gate is open.
        self.set_switch("SEED_DEMO_ACCOUNTS", "1")
        connection = database.get_db_connection()
        try:
            database.seed_demo_accounts(connection.cursor())
            connection.commit()
        finally:
            connection.close()
        self.assertIsNotNone(query_one("SELECT id FROM users WHERE email = ?", ("admin@demo.com",)))


class ReverseProxyTests(unittest.TestCase):
    """Behind one proxy hop the app must see the real scheme and client address.

    Render terminates TLS in front of the app and forwards the original scheme
    and address in headers. Without trusting exactly one hop, Flask sees plain
    HTTP from the proxy's IP: HSTS is never sent (the browser stays open to
    downgrade), and every visitor shares one rate-limit bucket. Trusting the
    headers when there is *no* proxy is just as wrong — a direct client could
    forge them — hence the explicit switch.
    """

    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in ("TRUST_PROXY", "FORCE_HTTPS", "COOKIE_SECURE")}

        def restore():
            for name, value in self._saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.addCleanup(restore)
        for name in self._saved:
            os.environ.pop(name, None)

    def _client(self, secure_cookie_seen: list):
        """A throwaway app wired exactly like the real one."""
        flask = Flask("proxy-probe-test")
        security.configure_app(flask, "test-secret-key-not-for-production")

        @flask.route("/api/whoami")
        def whoami():
            secure_cookie_seen.append(bool(flask.config["SESSION_COOKIE_SECURE"]))
            return {"ip": security.client_ip(), "secure": request_is_secure()}

        return flask.test_client()

    def test_forwarded_headers_are_ignored_without_the_switch(self):
        seen: list = []
        client = self._client(seen)
        response = client.get(
            "/api/whoami",
            headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
        )
        body = response.get_json()
        self.assertFalse(body["secure"], "the scheme header must not be honoured without TRUST_PROXY")
        self.assertNotEqual(body["ip"], "203.0.113.9", "a spoofed X-Forwarded-For must not pick the rate-limit bucket")
        self.assertNotIn("Strict-Transport-Security", response.headers)

    def test_one_trusted_hop_restores_https_and_the_real_client_ip(self):
        os.environ["TRUST_PROXY"] = "1"
        os.environ["COOKIE_SECURE"] = "1"
        seen: list = []
        client = self._client(seen)
        response = client.get(
            "/api/whoami",
            headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
        )
        body = response.get_json()
        self.assertTrue(body["secure"])
        self.assertEqual(body["ip"], "203.0.113.9")
        self.assertEqual(response.headers["Strict-Transport-Security"], "max-age=31536000; includeSubDomains")
        self.assertTrue(seen[0], "COOKIE_SECURE=1 must mark the session cookie Secure")

    def test_plain_http_is_redirected_only_when_asked_for(self):
        seen: list = []
        client = self._client(seen)
        self.assertEqual(client.get("/api/whoami").status_code, 200)

        os.environ["FORCE_HTTPS"] = "1"
        redirect = self._client(seen).get("/api/whoami")
        self.assertEqual(redirect.status_code, 301, "a GET is safely redirected with 301")
        self.assertEqual(redirect.headers["Location"], "https://localhost/api/whoami")

        write = self._client(seen).post("/api/whoami")
        self.assertEqual(write.status_code, 308, "a write must keep its method (308, not 301)")
        self.assertEqual(write.headers["Location"], "https://localhost/api/whoami")

    def test_the_redirect_is_skipped_for_an_already_secure_request(self):
        os.environ["TRUST_PROXY"] = "1"
        os.environ["FORCE_HTTPS"] = "1"
        seen: list = []
        response = self._client(seen).get("/api/whoami", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(response.status_code, 200)


def request_is_secure() -> bool:
    """Read the scheme inside a request context (kept out of the test bodies)."""
    from flask import request

    return bool(request.is_secure)


class CrossOriginHeaderTests(ApiTestCase):
    """No wildcard CORS: the API must not invite other origins in by default."""

    def test_no_access_control_header_without_configured_origins(self):
        self.assertEqual(os.environ.get("ALLOWED_ORIGINS", ""), "", "suite must run without extra origins")
        response = self.client.get("/api/roles", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_credentials_are_never_allowed_from_another_origin(self):
        response = self.client.get("/api/roles", headers={"Origin": "https://evil.example"})
        self.assertNotEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")


class VercelEntrypointTests(unittest.TestCase):
    """The serverless entrypoint must serve the hardened app, not a copy of it.

    It used to ship a second API with wildcard CORS, no authentication, a
    caller-supplied ``user_id`` and raw exception text in every error — the
    duplicate surface is what kept those holes alive after the main app was
    fixed.
    """

    def setUp(self):
        self.entrypoint = os.path.join(os.path.dirname(BACKEND_DIR), "api", "index.py")
        if not os.path.exists(self.entrypoint):
            self.skipTest("api/index.py is not checked out")

    def test_source_has_no_second_api_surface(self):
        """Only executable code is scanned: the docstring names the old holes on
        purpose, so it must not count as one of them."""
        with open(self.entrypoint, encoding="utf-8") as handle:
            source = handle.read()

        tree = ast.parse(source)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (
                isinstance(body, list)
                and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:]  # drop the docstring
        code = ast.unparse(tree)

        for banned in ("CORS(", "str(e)", "@app.route", "user_id"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, code, f"{banned} must not come back to the entrypoint")

    def test_entrypoint_exports_the_hardened_application(self):
        spec = importlib.util.spec_from_file_location("vercel_entrypoint", self.entrypoint)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIs(module.handler, app_module.app)
        self.assertIs(module.application, app_module.app)


if __name__ == "__main__":
    unittest.main()
