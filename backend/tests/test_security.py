"""Security regression tests: hashing, headers, throttling, CSRF, IDOR, error hygiene."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import unittest
from unittest.mock import patch

import security
from support import BACKEND_DIR, ApiTestCase, execute, fetch_text, flask_app, query_one


class PasswordHashingTests(unittest.TestCase):
    def test_hash_uses_a_stretched_kdf(self):
        stored = security.hash_password("Tr4iningTime")
        self.assertTrue(stored.startswith("pbkdf2:sha256:"))
        self.assertNotIn("Tr4iningTime", stored)

    def test_hash_is_salted_per_call(self):
        self.assertNotEqual(security.hash_password("Tr4iningTime"), security.hash_password("Tr4iningTime"))

    def test_round_trip(self):
        stored = security.hash_password("Tr4iningTime")
        self.assertTrue(security.verify_password("Tr4iningTime", stored))
        self.assertFalse(security.verify_password("Tr4iningTim3", stored))

    def test_modern_hashes_never_need_rehashing(self):
        self.assertFalse(security.needs_rehash(security.hash_password("Tr4iningTime")))

    def test_legacy_sha256_hash_still_verifies_and_is_flagged(self):
        salt = secrets.token_hex(16)
        legacy = f"{salt}:{hashlib.sha256(('Tr4iningTime' + salt).encode()).hexdigest()}"
        self.assertTrue(security.verify_password("Tr4iningTime", legacy))
        self.assertFalse(security.verify_password("wrong", legacy))
        self.assertTrue(security.needs_rehash(legacy))

    def test_malformed_hashes_do_not_raise(self):
        for broken in ("", "notahash", ":", "salt:", "pbkdf2:sha256:broken", None):
            with self.subTest(value=broken):
                self.assertFalse(security.verify_password("Tr4iningTime", broken))

    def test_shipped_iteration_count_is_not_weakened(self):
        """The suite lowers the cost for speed; the shipped default must stay strong."""
        with open(os.path.join(BACKEND_DIR, "security.py"), "r", encoding="utf-8") as handle:
            source = handle.read()
        match = re.search(r'PBKDF2_METHOD\s*=\s*"pbkdf2:sha256:(\d+)"', source)
        self.assertIsNotNone(match, "PBKDF2_METHOD is not declared as expected")
        self.assertGreaterEqual(int(match.group(1)), 600_000)

    def test_legacy_verification_only_accepts_the_exact_password(self):
        salt = secrets.token_hex(16)
        legacy = f"{salt}:{hashlib.sha256(('Tr4iningTime' + salt).encode()).hexdigest()}"
        self.assertFalse(security.verify_password("tr4iningtime", legacy))
        self.assertFalse(security.verify_password("Tr4iningTime ", legacy))


class LegacyUpgradeTests(ApiTestCase):
    def test_login_upgrades_a_legacy_hash(self):
        email = self.unique_email("legacy")
        password = "Tr4iningTime"
        salt = secrets.token_hex(16)
        legacy = f"{salt}:{hashlib.sha256((password + salt).encode()).hexdigest()}"
        execute(
            "INSERT INTO users (name, email, password_hash, is_verified) VALUES (?, ?, ?, 1)",
            ("Legacy User", email, legacy),
        )

        response = self.login(email, password)
        self.assertEqual(response.status_code, 200, response.get_json())

        stored = query_one("SELECT password_hash FROM users WHERE email = ?", (email,))["password_hash"]
        self.assertTrue(stored.startswith("pbkdf2:"), "legacy hash must be upgraded at login")
        self.assertTrue(security.verify_password(password, stored))


class SecurityHeaderTests(ApiTestCase):
    def test_html_responses_carry_the_hardening_headers(self):
        headers = fetch_text(self.client, "/login.html")[0].headers
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("Content-Security-Policy", headers)

    def test_api_responses_carry_the_hardening_headers(self):
        headers = self.client.get("/api/roles").headers
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("Content-Security-Policy", headers)

    def test_csp_locks_down_the_dangerous_directives(self):
        csp = fetch_text(self.client, "/")[0].headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("base-uri 'self'", csp)
        self.assertIn("form-action 'self'", csp)
        self.assertIn("default-src 'self'", csp)

    def test_hsts_is_not_sent_over_plain_http(self):
        self.assertNotIn("Strict-Transport-Security", fetch_text(self.client, "/")[0].headers)


class RateLimitTests(ApiTestCase):
    def test_login_is_throttled(self):
        email, password = self.signup_verified()
        security.LIMITER.reset()

        statuses = [self.login(email, "Wr0ngPassword9").status_code for _ in range(22)]
        self.assertIn(429, statuses, "repeated bad logins must eventually be throttled")
        self.assertEqual(statuses[0], 401)

        blocked = self.login(email, "Wr0ngPassword9")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_correct_password_is_also_blocked_while_throttled(self):
        email, password = self.signup_verified()
        security.LIMITER.reset()
        for _ in range(21):
            self.login(email, "Wr0ngPassword9")

        self.assertEqual(self.login(email, password).status_code, 429)

    def test_signup_is_throttled(self):
        security.LIMITER.reset()
        statuses = []
        for index in range(7):
            response, _, _ = self.signup(email=self.unique_email(f"burst{index}"))
            statuses.append(response.status_code)
        self.assertIn(429, statuses)

    def test_forgot_password_is_throttled(self):
        email, _ = self.signup_verified()
        security.LIMITER.reset()
        statuses = [
            self.client.post("/api/auth/forgot-password", json={"email": email}).status_code
            for _ in range(5)
        ]
        self.assertIn(429, statuses)

    def test_normal_use_is_not_throttled(self):
        """Guests may finish a practice session; the guard must not block that."""
        security.LIMITER.reset()
        response = self.client.post(
            "/api/submit-interview",
            json={
                "role": "Software Engineer",
                "answers": [{"question_id": 1, "transcript": "I shipped it on time."}],
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_ai_endpoints_are_throttled_without_a_session(self):
        """These routes reach the AI engine, so an anonymous caller must not be
        able to loop them: unlimited AI calls are both a cost and a DoS risk."""

        def submit():
            return self.client.post(
                "/api/submit-interview",
                json={
                    "role": "Software Engineer",
                    "answers": [{"question_id": 1, "transcript": "I shipped it on time."}],
                },
            )

        def questions():
            return self.client.get("/api/questions?role=Software%20Engineer")

        for label, call, limit in (
            ("submit-interview", submit, 20),
            ("questions", questions, 60),
        ):
            with self.subTest(endpoint=label):
                security.LIMITER.reset()
                self.assertNotIn(429, [call().status_code for _ in range(limit)])
                blocked = call()
                self.assertEqual(blocked.status_code, 429, f"{label} must be throttled")
                self.assertIn("Retry-After", blocked.headers)

    def test_analyze_resume_and_ask_endpoints_are_throttled(self):
        from io import BytesIO

        security.LIMITER.reset()
        for index in range(10):
            self.client.post(
                "/api/analyze-resume",
                data={"file": (BytesIO(b"my resume"), f"resume{index}.txt")},
                content_type="multipart/form-data",
            )
        blocked = self.client.post(
            "/api/analyze-resume",
            data={"file": (BytesIO(b"my resume"), "resume.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(blocked.status_code, 429)

        security.LIMITER.reset()
        for _ in range(30):
            self.client.post("/api/ask-resume-question", json={"question": "What is my degree?"})
        blocked = self.client.post("/api/ask-resume-question", json={"question": "What is my degree?"})
        self.assertEqual(blocked.status_code, 429)

    def test_limiter_reset_clears_the_counter(self):
        security.LIMITER.reset()
        allowed, _ = security.LIMITER.check("unit:test", limit=1, window_seconds=60)
        self.assertTrue(allowed)
        blocked, retry_after = security.LIMITER.check("unit:test", limit=1, window_seconds=60)
        self.assertFalse(blocked)
        self.assertGreaterEqual(retry_after, 1)

        security.LIMITER.reset()
        allowed_again, _ = security.LIMITER.check("unit:test", limit=1, window_seconds=60)
        self.assertTrue(allowed_again)


class CrossSiteRequestTests(ApiTestCase):
    def test_cross_origin_write_is_blocked(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email": "a@b.co", "password": "Str0ngPass1"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_same_origin_write_is_allowed(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email": "a@b.co", "password": "Str0ngPass1"},
            headers={"Origin": "http://localhost"},
        )
        # Reaches the view (401 for bad credentials) rather than the CSRF guard.
        self.assertEqual(response.status_code, 401)

    def test_cross_origin_read_is_allowed(self):
        self.assertEqual(self.client.get("/api/roles", headers={"Origin": "https://evil.example"}).status_code, 200)

    def test_cross_origin_write_without_origin_header_is_allowed(self):
        # Non-browser clients (curl, server-to-server) send no Origin.
        response = self.client.post("/api/auth/login", json={"email": "a@b.co", "password": "x"})
        self.assertEqual(response.status_code, 401)


class AccessControlTests(ApiTestCase):
    """The old API trusted a client-supplied `user_id` for every read and write."""

    def _submit_interview(self, client, *, user_id=None):
        payload = {
            "role": "Software Engineer",
            "answers": [
                {
                    "question_id": 1,
                    "transcript": "I resolved the disagreement by comparing trade-offs and agreeing on Postgres.",
                }
            ],
        }
        if user_id is not None:
            payload["user_id"] = user_id
        return client.post("/api/submit-interview", json=payload)

    def test_dashboard_cannot_be_pointed_at_another_account(self):
        owner, _ = self.signed_in_client(name="Owner User")
        self.assertEqual(self._submit_interview(owner).status_code, 200)
        owner_id = owner.get("/api/auth/me").get_json()["id"]
        self.assertEqual(owner.get("/api/dashboard").get_json()["metrics"]["total_interviews"], 1)

        attacker, _ = self.signed_in_client(name="Attacker User")
        response = attacker.get(f"/api/dashboard?user_id={owner_id}")
        self.assertEqual(response.status_code, 200)

        body = response.get_json()
        # The parameter is ignored: the attacker sees their own empty dashboard.
        self.assertEqual(body["user"]["name"], "Attacker User")
        self.assertEqual(body["metrics"]["total_interviews"], 0)

    def test_interview_detail_is_scoped_to_the_owner(self):
        owner, _ = self.signed_in_client()
        created = self._submit_interview(owner)
        self.assertEqual(created.status_code, 200)
        interview_id = created.get_json()["interview_id"]

        self.assertEqual(owner.get(f"/api/interview/{interview_id}").status_code, 200)

        attacker, _ = self.signed_in_client()
        self.assertEqual(attacker.get(f"/api/interview/{interview_id}").status_code, 404)

    def test_interview_detail_requires_authentication(self):
        owner, _ = self.signed_in_client()
        interview_id = self._submit_interview(owner).get_json()["interview_id"]
        self.assertEqual(self.new_client().get(f"/api/interview/{interview_id}").status_code, 401)

    def test_submit_interview_ignores_a_spoofed_user_id(self):
        victim, _ = self.signed_in_client()
        victim_id = victim.get("/api/auth/me").get_json()["id"]

        attacker, _ = self.signed_in_client()
        attacker_id = attacker.get("/api/auth/me").get_json()["id"]

        created = self._submit_interview(attacker, user_id=victim_id)
        self.assertEqual(created.status_code, 200)

        owner_of_row = query_one(
            "SELECT user_id FROM interviews WHERE id = ?", (created.get_json()["interview_id"],)
        )["user_id"]
        self.assertEqual(owner_of_row, attacker_id)
        self.assertNotEqual(owner_of_row, victim_id)

    def test_guest_submission_is_not_filed_under_a_real_account(self):
        response = self._submit_interview(self.new_client())
        self.assertEqual(response.status_code, 200)

        owner_of_row = query_one(
            "SELECT user_id FROM interviews WHERE id = ?", (response.get_json()["interview_id"],)
        )["user_id"]
        self.assertIsNone(owner_of_row, "guests must not write into a real account")

    def test_profile_cannot_be_edited_across_accounts(self):
        victim, victim_email = self.signed_in_client()
        victim_id = victim.get("/api/auth/me").get_json()["id"]
        original_name = query_one("SELECT name FROM users WHERE id = ?", (victim_id,))["name"]

        attacker, _ = self.signed_in_client()
        response = attacker.post(
            "/api/auth/update-profile",
            json={"user_id": victim_id, "name": "Pwned"},
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(query_one("SELECT name FROM users WHERE id = ?", (victim_id,))["name"], original_name)

    def test_user_lookup_no_longer_enumerates_accounts(self):
        self.assertEqual(self.new_client().get("/api/auth/user?user_id=1").status_code, 401)


class InputHardeningTests(ApiTestCase):
    def test_malformed_json_is_a_400_not_a_500(self):
        response = self.client.post(
            "/api/submit-interview", data="{not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_oversized_body_is_rejected(self):
        original = flask_app.config["MAX_CONTENT_LENGTH"]
        flask_app.config["MAX_CONTENT_LENGTH"] = 512
        try:
            response = self.client.post(
                "/api/submit-interview",
                data="x" * 5000,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 413)
        finally:
            flask_app.config["MAX_CONTENT_LENGTH"] = original

    def test_question_limit_is_bounded(self):
        response = self.client.get("/api/questions?role=Software%20Engineer&limit=999999")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.get_json()["questions"]), 25)

    def test_feedback_ratings_are_clamped_and_comment_truncated(self):
        response = self.client.post(
            "/api/feedback",
            json={
                "interview_rating": 99,
                "website_rating": -5,
                "comment": "c" * 5000,
                "user": "someone@example.com",
            },
        )
        self.assertEqual(response.status_code, 200)

        row = query_one(
            "SELECT interview_rating, website_rating, comment FROM feedback ORDER BY id DESC LIMIT 1"
        )
        self.assertEqual(row["interview_rating"], 5)
        self.assertEqual(row["website_rating"], 0)
        self.assertLessEqual(len(row["comment"]), 2000)

    def test_sql_injection_attempts_do_not_authenticate(self):
        payloads = ["' OR '1'='1", "admin@example.com' --", "' OR 1=1 --"]
        for payload in payloads:
            with self.subTest(payload=payload):
                security.LIMITER.reset()
                response = self.client.post(
                    "/api/auth/login", json={"email": payload, "password": payload}
                )
                self.assertEqual(response.status_code, 401)

    def test_sql_injection_in_signup_is_rejected_as_a_bad_email(self):
        response = self.client.post(
            "/api/auth/signup",
            json={"name": "Bobby", "email": "a' OR '1'='1@x.com", "password": "Str0ngPass1"},
        )
        self.assertEqual(response.status_code, 400)


class ErrorHygieneTests(ApiTestCase):
    def test_internal_errors_do_not_leak_details(self):
        with patch("app.get_db_connection", side_effect=RuntimeError("boom: dsn=postgres://secret")):
            response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.mimetype, "application/json")

        body = response.get_data(as_text=True)
        self.assertNotIn("boom", body)
        self.assertNotIn("secret", body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("RuntimeError", body)

        # The client still gets a well-formed, generic JSON error.
        payload = response.get_json()
        self.assertIsInstance(payload, dict)
        self.assertTrue(payload.get("error"))

    def test_unknown_api_route_returns_json_404(self):
        response = self.client.get("/api/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertIsInstance(response.get_json(), dict)

    def test_unknown_page_returns_the_designed_404_with_status(self):
        response, body = fetch_text(self.client, "/definitely-not-a-page")
        self.assertEqual(response.status_code, 404)
        self.assertIn("404", body)


class StartupPortTests(unittest.TestCase):
    """The served port has to stay predictable.

    A shell that exports PORT=0 (meaning "unset") used to win over the
    PORT=5000 in .env, because load_dotenv() never overwrites a variable that
    already exists. The dev server then bound a random ephemeral port, so the
    documented http://localhost:5000 URL quietly stopped working.
    """

    def _resolve(self, port):
        from app import resolve_port

        env = dict(os.environ)
        env.pop("PORT", None)
        if port is not None:
            env["PORT"] = port
        with patch.dict(os.environ, env, clear=True):
            return resolve_port()

    def test_default_is_the_documented_port(self):
        self.assertEqual(self._resolve(None), 5000)

    def test_configured_port_is_honoured(self):
        self.assertEqual(self._resolve("8080"), 8080)

    def test_zero_falls_back_instead_of_binding_a_random_port(self):
        self.assertEqual(self._resolve("0"), 5000)

    def test_nonsense_values_fall_back_to_the_default(self):
        for bad in ("", "   ", "abc", "-1", "5000.5", "0x50"):
            with self.subTest(port=bad):
                self.assertEqual(self._resolve(bad), 5000)

    def test_dotenv_port_matches_the_documented_url(self):
        env_file = os.path.join(os.path.dirname(BACKEND_DIR), ".env")
        if not os.path.exists(env_file):
            self.skipTest(".env is not checked out")
        with open(env_file, encoding="utf-8") as handle:
            contents = handle.read()
        match = re.search(r"^PORT=(\d+)", contents, re.MULTILINE)
        self.assertIsNotNone(match, ".env should pin PORT so the URL is stable")
        self.assertEqual(self._resolve(match.group(1)), 5000)


if __name__ == "__main__":
    unittest.main()
