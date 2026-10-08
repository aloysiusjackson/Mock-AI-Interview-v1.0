"""End-to-end tests for the authentication flows."""

from __future__ import annotations

import os
import re
import unittest
from datetime import datetime, timedelta
from unittest import mock

import security
from support import (
    BACKEND_DIR, ApiTestCase, app_module, execute, query_one, sent_emails, token_from_link,
)


class SignupTests(ApiTestCase):
    def test_signup_creates_an_unverified_account(self):
        response, email, _ = self.signup(name="Ada Lovelace")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])

        row = query_one("SELECT name, email, is_verified, password_hash FROM users WHERE email = ?", (email,))
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Ada Lovelace")
        self.assertEqual(row["is_verified"], 0)
        self.assertTrue(row["password_hash"])

    def test_signup_never_stores_the_plain_password(self):
        _, email, password = self.signup()
        row = query_one("SELECT password_hash FROM users WHERE email = ?", (email,))
        self.assertNotIn(password, row["password_hash"])
        self.assertTrue(row["password_hash"].startswith("pbkdf2:"))

    def test_signup_rejects_duplicate_email(self):
        response, email, _ = self.signup()
        self.assertEqual(response.status_code, 200)
        security.LIMITER.reset()
        duplicate = self.client.post(
            "/api/auth/signup",
            json={"name": "Someone Else", "email": email, "password": "Str0ngPass1"},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_signup_normalises_email_case(self):
        email = self.unique_email(prefix="Mixed")
        response = self.client.post(
            "/api/auth/signup",
            json={"name": "Case Test", "email": email.upper(), "password": "Str0ngPass1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(query_one("SELECT id FROM users WHERE email = ?", (email.lower(),)))

    def test_signup_rejects_weak_passwords(self):
        weak = [
            "short1",            # under the length floor
            "alllettersonly",    # no digit
            "1234567890",        # no letter
            "password",          # well-known
            "password123",       # well-known
            "x" * 129,           # over the length ceiling
        ]
        for index, password in enumerate(weak):
            with self.subTest(password=password):
                security.LIMITER.reset()
                response = self.client.post(
                    "/api/auth/signup",
                    json={"name": "Weak Pass", "email": self.unique_email(f"weak{index}"), "password": password},
                )
                self.assertEqual(response.status_code, 400)

    def test_signup_rejects_password_containing_the_name(self):
        response = self.client.post(
            "/api/auth/signup",
            json={"name": "Jackson", "email": self.unique_email("jackson"), "password": "Jackson12345"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name or email", response.get_json()["error"])

    def test_signup_rejects_bad_email_or_missing_fields(self):
        cases = [
            {"name": "A", "email": "not-an-email", "password": "Str0ngPass1"},
            {"name": "A", "email": "missing@tld", "password": "Str0ngPass1"},
            {"name": "", "email": "someone@example.com", "password": "Str0ngPass1"},
            {"name": "A", "email": "", "password": "Str0ngPass1"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                security.LIMITER.reset()
                response = self.client.post("/api/auth/signup", json=payload)
                self.assertEqual(response.status_code, 400)

    def test_signup_handles_a_missing_body_without_a_500(self):
        response = self.client.post("/api/auth/signup", data="not json", content_type="text/plain")
        self.assertEqual(response.status_code, 400)

    def test_signup_queues_a_verification_email(self):
        _, email, _ = self.signup()
        kinds = [mail["kind"] for mail in sent_emails]
        self.assertIn("verification", kinds)
        self.assertTrue(any(mail["email"] == email for mail in sent_emails))


class VerificationTests(ApiTestCase):
    def test_verification_link_activates_the_account(self):
        response, email, _ = self.signup()
        token = token_from_link(response.get_json()["verification_link"])

        verified = self.client.get(f"/api/auth/verify-email?token={token}")
        self.assertEqual(verified.status_code, 200)
        self.assertIn("Email Verified", verified.get_data(as_text=True))

        row = query_one("SELECT is_verified, verification_token FROM users WHERE email = ?", (email,))
        self.assertEqual(row["is_verified"], 1)
        self.assertIsNone(row["verification_token"], "token must be consumed")

    def test_verification_token_is_single_use(self):
        response, _, _ = self.signup()
        token = token_from_link(response.get_json()["verification_link"])
        self.client.get(f"/api/auth/verify-email?token={token}")

        replay = self.client.get(f"/api/auth/verify-email?token={token}")
        self.assertEqual(replay.status_code, 400)

    def test_unknown_verification_token_is_rejected(self):
        response = self.client.get("/api/auth/verify-email?token=definitely-not-a-token")
        self.assertEqual(response.status_code, 400)

    def test_signup_records_an_expiry_for_the_link(self):
        response, email, _ = self.signup()
        self.assertEqual(response.status_code, 200)

        row = query_one(
            "SELECT verification_token_expires FROM users WHERE email = ?", (email,)
        )
        self.assertTrue(row["verification_token_expires"], "a link must carry an expiry")

        with open(os.path.join(BACKEND_DIR, "app.py"), encoding="utf-8") as handle:
            ttl = re.search(r"VERIFICATION_LINK_TTL_HOURS\s*=\s*(\d+)", handle.read())
        self.assertIsNotNone(ttl, "the TTL must stay a single named constant")
        self.assertGreaterEqual(int(ttl.group(1)), 1)
        self.assertLessEqual(int(ttl.group(1)), 72, "a verification link should not stay live for days")

    def test_an_expired_link_is_rejected_and_retired(self):
        """A leaked link must stop working once its timestamp is in the past."""
        response, email, _ = self.signup()
        token = token_from_link(response.get_json()["verification_link"])

        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        execute(
            "UPDATE users SET verification_token_expires = ? WHERE email = ?", (past, email)
        )

        expired = self.client.get(f"/api/auth/verify-email?token={token}")
        self.assertEqual(expired.status_code, 400)
        self.assertIn("Expired", expired.get_data(as_text=True))

        row = query_one("SELECT is_verified, verification_token FROM users WHERE email = ?", (email,))
        self.assertEqual(row["is_verified"], 0, "an expired link must not verify anything")
        self.assertIsNone(row["verification_token"], "the stale token must be retired")

        # The same link cannot be replayed after being retired.
        self.assertEqual(self.client.get(f"/api/auth/verify-email?token={token}").status_code, 400)

    def test_a_link_without_a_timestamp_still_works(self):
        """Rows predating the column (NULL expiry) must keep working."""
        response, email, _ = self.signup()
        token = token_from_link(response.get_json()["verification_link"])
        execute("UPDATE users SET verification_token_expires = NULL WHERE email = ?", (email,))

        verified = self.client.get(f"/api/auth/verify-email?token={token}")
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(
            query_one("SELECT is_verified FROM users WHERE email = ?", (email,))["is_verified"], 1
        )

    def test_resending_a_link_refreshes_its_expiry(self):
        response, email, _ = self.signup()
        old_token = token_from_link(response.get_json()["verification_link"])

        stale = (datetime.now() - timedelta(hours=1)).isoformat()
        execute("UPDATE users SET verification_token_expires = ? WHERE email = ?", (stale, email))

        resent = self.client.post("/api/auth/resend-verification", json={"email": email})
        self.assertEqual(resent.status_code, 200)
        new_token = token_from_link(resent.get_json()["verification_link"])

        row = query_one(
            "SELECT verification_token_expires FROM users WHERE email = ?", (email,)
        )
        self.assertGreater(
            datetime.fromisoformat(row["verification_token_expires"]), datetime.now()
        )
        self.assertEqual(self.client.get(f"/api/auth/verify-email?token={old_token}").status_code, 400)
        self.assertEqual(self.client.get(f"/api/auth/verify-email?token={new_token}").status_code, 200)

    def test_resend_verification_does_not_leak_account_existence(self):
        known_response, _, _ = self.signup()
        known = known_response.get_json()["message"] != ""
        self.assertTrue(known)

        unknown = self.client.post(
            "/api/auth/resend-verification", json={"email": self.unique_email("ghost")}
        )
        self.assertEqual(unknown.status_code, 200)
        self.assertTrue(unknown.get_json()["success"])


class LoginTests(ApiTestCase):
    def test_login_requires_verification_first(self):
        _, email, password = self.signup()
        response = self.login(email, password)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.get_json()["needs_verification"])

    def test_login_succeeds_after_verification(self):
        email, password = self.signup_verified()
        response = self.login(email, password)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(response.get_json()["user"]["email"], email)

    def test_login_opens_a_server_side_session(self):
        email, password = self.signup_verified()
        self.login(email, password)

        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["email"], email)

    def test_session_cookie_is_httponly(self):
        email, password = self.signup_verified()
        response = self.login(email, password)
        cookies = response.headers.getlist("Set-Cookie")
        self.assertTrue(cookies, "login must set a session cookie")
        session_cookie = next(c for c in cookies if c.startswith("session="))
        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("SameSite=Lax", session_cookie)

    def test_wrong_password_and_unknown_email_are_indistinguishable(self):
        email, _ = self.signup_verified()
        security.LIMITER.reset()

        wrong_password = self.login(email, "Wr0ngPassword9")
        unknown_email = self.login(self.unique_email("nobody"), "Wr0ngPassword9")

        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(unknown_email.status_code, 401)
        self.assertEqual(wrong_password.get_json()["error"], unknown_email.get_json()["error"])

    def test_login_requires_both_credentials(self):
        self.assertEqual(self.client.post("/api/auth/login", json={"email": "a@b.co"}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/login", json={"password": "Str0ngPass1"}).status_code, 400)

    def test_google_only_account_is_told_to_use_google(self):
        email = self.unique_email("googleuser")
        execute(
            "INSERT INTO users (name, email, google_id, is_verified) VALUES (?, ?, ?, 1)",
            ("Google User", email, "google-sub-123"),
        )
        response = self.login(email, "Str0ngPass1")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Google", response.get_json()["error"])

    def test_logout_clears_the_session(self):
        email, password = self.signup_verified()
        self.login(email, password)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)


class SessionEndpointTests(ApiTestCase):
    def test_me_requires_a_session(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_me_ignores_a_supplied_user_id(self):
        client, _ = self.signed_in_client()
        other_id = query_one("SELECT id FROM users WHERE email = 'demo@example.com'")["id"]
        response = client.get(f"/api/auth/me?user_id={other_id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.get_json()["id"], other_id)

    def test_update_profile_changes_the_signed_in_user(self):
        client, email = self.signed_in_client()
        response = client.post(
            "/api/auth/update-profile",
            json={"name": "Renamed User", "target_role": "Data Analyst"},
        )
        self.assertEqual(response.status_code, 200)

        row = query_one("SELECT name, target_role FROM users WHERE email = ?", (email,))
        self.assertEqual(row["name"], "Renamed User")
        self.assertEqual(row["target_role"], "Data Analyst")

    def test_update_profile_requires_authentication(self):
        response = self.client.post("/api/auth/update-profile", json={"name": "Nope"})
        self.assertEqual(response.status_code, 401)


class PasswordResetTests(ApiTestCase):
    def test_full_reset_cycle(self):
        email, _ = self.signup_verified()
        security.LIMITER.reset()

        forgot = self.client.post("/api/auth/forgot-password", json={"email": email})
        self.assertEqual(forgot.status_code, 200)
        token = token_from_link(forgot.get_json()["reset_link"])

        reset = self.client.post(
            "/api/auth/reset-password", json={"token": token, "password": "BrandNewPass9"}
        )
        self.assertEqual(reset.status_code, 200)

        security.LIMITER.reset()
        self.assertEqual(self.login(email, "BrandNewPass9").status_code, 200)
        security.LIMITER.reset()
        self.assertEqual(self.login(email, "Str0ngPass1").status_code, 401)

    def test_reset_token_is_single_use(self):
        email, _ = self.signup_verified()
        security.LIMITER.reset()
        forgot = self.client.post("/api/auth/forgot-password", json={"email": email})
        token = token_from_link(forgot.get_json()["reset_link"])

        first = self.client.post("/api/auth/reset-password", json={"token": token, "password": "FirstPass123"})
        self.assertEqual(first.status_code, 200)

        replay = self.client.post("/api/auth/reset-password", json={"token": token, "password": "SecondPass123"})
        self.assertEqual(replay.status_code, 400)

    def test_expired_reset_token_is_rejected(self):
        email, _ = self.signup_verified()
        security.LIMITER.reset()
        forgot = self.client.post("/api/auth/forgot-password", json={"email": email})
        token = token_from_link(forgot.get_json()["reset_link"])

        execute(
            "UPDATE users SET reset_token_expires = ? WHERE email = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(), email),
        )

        response = self.client.post(
            "/api/auth/reset-password", json={"token": token, "password": "TooLatePass9"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.get_json()["error"].lower())

    def test_reset_enforces_the_password_policy(self):
        email, _ = self.signup_verified()
        security.LIMITER.reset()
        forgot = self.client.post("/api/auth/forgot-password", json={"email": email})
        token = token_from_link(forgot.get_json()["reset_link"])

        response = self.client.post("/api/auth/reset-password", json={"token": token, "password": "weak"})
        self.assertEqual(response.status_code, 400)

    def test_reset_does_not_mark_the_account_verified(self):
        response, email, _ = self.signup()  # deliberately left unverified
        security.LIMITER.reset()
        forgot = self.client.post("/api/auth/forgot-password", json={"email": email})
        token = token_from_link(forgot.get_json()["reset_link"])

        self.client.post("/api/auth/reset-password", json={"token": token, "password": "FreshPass123"})
        row = query_one("SELECT is_verified FROM users WHERE email = ?", (email,))
        self.assertEqual(row["is_verified"], 0, "resetting a password must not bypass email verification")

    def test_forgot_password_does_not_reveal_registered_addresses(self):
        known, _ = self.signup_verified()
        security.LIMITER.reset()

        first = self.client.post("/api/auth/forgot-password", json={"email": known})
        security.LIMITER.reset()
        second = self.client.post("/api/auth/forgot-password", json={"email": self.unique_email("ghost")})

        self.assertEqual(first.status_code, second.status_code)
        self.assertEqual(first.get_json().get("success"), second.get_json().get("success"))
        self.assertNotIn("reset_link", second.get_json(), "must not mint a link for an unknown address")

    def test_forgot_password_rejects_a_malformed_address(self):
        response = self.client.post("/api/auth/forgot-password", json={"email": "nope"})
        self.assertEqual(response.status_code, 400)


class PasswordPolicyUnitTests(unittest.TestCase):
    def test_accepts_a_reasonable_password(self):
        self.assertIsNone(security.validate_password("Tr4iningTime"))

    def test_rejects_each_rule(self):
        cases = {
            "short": "Ab1",
            "no digit": "onlyletters",
            "no letter": "1234567890",
            "too long": "a1" + "b" * 200,
            "common": "password123",
        }
        for label, password in cases.items():
            with self.subTest(rule=label):
                self.assertIsNotNone(security.validate_password(password))

    def test_rejects_password_built_from_the_email(self):
        self.assertIsNotNone(
            security.validate_password("Aloysius123", email="aloysius@example.com")
        )

    def test_non_string_input_is_rejected(self):
        self.assertIsNotNone(security.validate_password(None))
        self.assertIsNotNone(security.validate_password(12345678))


class _FakeGoogleResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class GoogleSignInTests(ApiTestCase):
    """The Google endpoint is exercised with both network calls stubbed out.

    support.py blanks GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET so the suite stays
    offline; this class sets test credentials and patches requests, so nothing
    ever reaches Google.
    """

    def setUp(self):
        super().setUp()
        self._saved = {
            key: os.environ.get(key)
            for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
        }
        os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
        os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
        self.addCleanup(self._restore_google_env)

    def _restore_google_env(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def google_signin(self, email, *, name="Google Person", sub=None, client=None):
        """Drive /api/auth/google with a stubbed token exchange."""
        client = client or self.client
        # Google's `sub` is unique per account, and the lookup matches on
        # email OR google_id, so reusing one sub would cross-link the tests.
        sub = sub or ("sub-" + email)
        claims = {
            "aud": "test-client-id.apps.googleusercontent.com",
            "iss": "https://accounts.google.com",
            "email": email,
            "email_verified": "true",
            "sub": sub,
            "name": name,
            "picture": "https://example.com/avatar.png",
        }
        with mock.patch.object(
            app_module.requests, "post", return_value=_FakeGoogleResponse(200, {"id_token": "fake-id-token"})
        ), mock.patch.object(
            app_module.requests, "get", return_value=_FakeGoogleResponse(200, claims)
        ):
            return client.post(
                "/api/auth/google",
                json={"code": "fake-code", "redirect_uri": "http://127.0.0.1:5000/login.html"},
            )

    def test_new_google_account_is_flagged_for_registration(self):
        response = self.google_signin(self.unique_email("googlenew"))
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertTrue(
            body["is_new_user"],
            "a first-time Google sign-in must be told to finish registration",
        )

    def test_returning_google_account_is_not_flagged(self):
        email = self.unique_email("googlereturn")
        first = self.google_signin(email)
        self.assertTrue(first.get_json()["is_new_user"])

        self.client.post("/api/auth/logout")
        second = self.google_signin(email)
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertFalse(
            second.get_json()["is_new_user"],
            "an existing Google account must skip the registration step",
        )

    def test_google_account_is_created_already_verified(self):
        email = self.unique_email("googleverified")
        self.google_signin(email)

        row = query_one("SELECT is_verified, google_id FROM users WHERE email = ?", (email,))
        self.assertIsNotNone(row)
        self.assertEqual(row["is_verified"], 1)
        self.assertEqual(row["google_id"], "sub-" + email)

    def test_google_account_can_complete_its_profile(self):
        email = self.unique_email("googlecomplete")
        self.google_signin(email)

        # The session is opened by the Google exchange, so the follow-up call the
        # completion panel makes must be authorised already.
        done = self.client.post(
            "/api/auth/update-profile",
            json={"name": "Completed Name", "target_role": "Data Analyst"},
        )
        self.assertEqual(done.status_code, 200, done.get_json())

        row = query_one("SELECT name, target_role FROM users WHERE email = ?", (email,))
        self.assertEqual(row["name"], "Completed Name")
        self.assertEqual(row["target_role"], "Data Analyst")

    def test_google_signin_links_an_existing_email_account(self):
        email, _ = self.signup_verified()

        security.LIMITER.reset()
        response = self.google_signin(email)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse(
            response.get_json()["is_new_user"],
            "an account that already exists must not be treated as new",
        )

        row = query_one("SELECT google_id FROM users WHERE email = ?", (email,))
        self.assertEqual(row["google_id"], "sub-" + email)


if __name__ == "__main__":
    unittest.main()
