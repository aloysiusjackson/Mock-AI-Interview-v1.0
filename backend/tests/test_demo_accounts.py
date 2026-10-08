"""Tests for the self-healing demo accounts.

Demo credentials are intentionally public (admin@demo.com / user@demo.com,
password Demo@1234). The suite verifies they are seeded, loginable, and
survive the admin Danger Zone resets so the app can never lock its owner out.
"""

from __future__ import annotations

import os
import unittest

import database
import security
from support import ApiTestCase, FRONTEND_DIR, execute, query_one

DEMO_ADMIN = "admin@demo.com"
DEMO_USER = "user@demo.com"
DEMO_PASSWORD = "Demo@1234"


class DemoAccountTests(ApiTestCase):
    # init_db() runs at import time, so both demo accounts already exist.

    def create_admin_user(self, name="Admin"):
        """Create a throwaway admin directly in the database."""
        import uuid

        email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
        password = "AdminPass123!"
        execute(
            "INSERT INTO users (name, email, password_hash, role, is_verified) "
            "VALUES (?, ?, ?, 'admin', 1)",
            (name, email, security.hash_password(password)),
        )
        return email, password

    def login_as_admin(self, name="Admin"):
        email, password = self.create_admin_user(name=name)
        response = self.client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return email, password

    def test_demo_accounts_are_seeded_and_verified(self):
        for email in (DEMO_ADMIN, DEMO_USER):
            row = query_one(
                "SELECT is_verified, password_hash, role FROM users WHERE email = ?",
                (email,),
            )
            self.assertIsNotNone(row, email)
            self.assertEqual(row["is_verified"], 1)
            self.assertTrue(row["password_hash"])

    def test_demo_roles(self):
        self.assertEqual(
            query_one("SELECT role FROM users WHERE email = ?", (DEMO_ADMIN,))["role"],
            "admin",
        )
        self.assertEqual(
            query_one("SELECT role FROM users WHERE email = ?", (DEMO_USER,))["role"],
            "user",
        )

    def test_demo_admin_can_log_in(self):
        response = self.client.post(
            "/api/auth/login", json={"email": DEMO_ADMIN, "password": DEMO_PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(response.get_json()["user"]["role"], "admin")

    def test_demo_user_can_log_in(self):
        response = self.client.post(
            "/api/auth/login", json={"email": DEMO_USER, "password": DEMO_PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["user"]["role"], "user")

    def test_legacy_demo_seed_gains_a_password(self):
        """demo@example.com historically seeded passwordless; it must log in now."""
        row = query_one(
            "SELECT password_hash FROM users WHERE email = 'demo@example.com'"
        )
        self.assertIsNotNone(row)
        self.assertTrue(row["password_hash"])
        response = self.client.post(
            "/api/auth/login",
            json={"email": "demo@example.com", "password": DEMO_PASSWORD},
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_reseeding_restores_a_damaged_demo_password(self):
        """A corrupted demo row is repaired by seed_demo_accounts()."""
        conn = database.get_db_connection()
        try:
            conn.execute(
                "UPDATE users SET password_hash = 'bogus' WHERE email = ?", (DEMO_USER,)
            )
            conn.commit()
            database.seed_demo_accounts(conn.cursor())
            conn.commit()
        finally:
            conn.close()

        response = self.client.post(
            "/api/auth/login", json={"email": DEMO_USER, "password": DEMO_PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_reseeding_restores_a_deleted_demo_account(self):
        """After the demo rows are deleted, seeding recreates them."""
        conn = database.get_db_connection()
        try:
            conn.execute("DELETE FROM users WHERE email IN (?, ?)", (DEMO_ADMIN, DEMO_USER))
            conn.commit()
            database.seed_demo_accounts(conn.cursor())
            conn.commit()
        finally:
            conn.close()

        for email, role in ((DEMO_ADMIN, "admin"), (DEMO_USER, "user")):
            row = query_one("SELECT role FROM users WHERE email = ?", (email,))
            self.assertIsNotNone(row, email)
            self.assertEqual(row["role"], role)
            response = self.client.post(
                "/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}
            )
            self.assertEqual(response.status_code, 200, response.get_json())

    def test_reset_users_preserves_demo_accounts(self):
        """'Reset Users' keeps the demo logins and deletes regular users."""
        email, _ = self.signup_verified(name="Doomed User")
        self.login_as_admin()

        response = self.client.post("/api/admin/reset-users")
        self.assertEqual(response.status_code, 200, response.get_json())

        self.assertIsNone(query_one("SELECT id FROM users WHERE email = ?", (email,)))
        for demo in (DEMO_ADMIN, DEMO_USER):
            self.assertIsNotNone(query_one("SELECT id FROM users WHERE email = ?", (demo,)))
            login = self.client.post(
                "/api/auth/login", json={"email": demo, "password": DEMO_PASSWORD}
            )
            self.assertEqual(login.status_code, 200, login.get_json())

    def test_reset_all_restores_demo_accounts_and_questions(self):
        """'Reset All' wipes everything but the demo accounts come back."""
        email, _ = self.signup_verified(name="Doomed User")
        self.login_as_admin()

        response = self.client.post("/api/admin/reset-all")
        self.assertEqual(response.status_code, 200, response.get_json())

        self.assertIsNone(query_one("SELECT id FROM users WHERE email = ?", (email,)))
        self.assertGreater(
            query_one("SELECT COUNT(*) AS cnt FROM questions")["cnt"], 0,
            "questions must be re-seeded after a full reset",
        )
        for demo, role in ((DEMO_ADMIN, "admin"), (DEMO_USER, "user")):
            row = query_one("SELECT role FROM users WHERE email = ?", (demo,))
            self.assertIsNotNone(row, demo)
            self.assertEqual(row["role"], role)
            login = self.client.post(
                "/api/auth/login", json={"email": demo, "password": DEMO_PASSWORD}
            )
            self.assertEqual(login.status_code, 200, login.get_json())

    def test_demo_password_is_never_stored_in_plain_text(self):
        row = query_one("SELECT password_hash FROM users WHERE email = ?", (DEMO_ADMIN,))
        self.assertNotIn(DEMO_PASSWORD, row["password_hash"])
        self.assertTrue(row["password_hash"].startswith("pbkdf2:"))


class DemoCredentialVisibilityTests(unittest.TestCase):
    """Live visitors must never see the demo credentials in the page markup.

    The credentials are injected by JS only when the URL carries ?demo=1, and
    they are base64-encoded in the source so a casual view-source reveals
    nothing.
    """

    PAGES = ("login.html", os.path.join("admin", "login.html"))

    def _read(self, page: str) -> str:
        with open(os.path.join(FRONTEND_DIR, page), encoding="utf-8") as fh:
            return fh.read()

    def test_login_pages_do_not_reveal_plain_credentials(self):
        for page in self.PAGES:
            html = self._read(page)
            self.assertNotIn("user@demo.com", html, page)
            self.assertNotIn("admin@demo.com", html, page)
            self.assertNotIn("Demo@1234", html, page)

    def test_demo_box_exists_but_is_gated_and_hidden(self):
        for page in self.PAGES:
            html = self._read(page)
            self.assertIn('id="demo-creds"', html, page)
            self.assertIn("hidden", html, page)
            # The injection only runs with the private ?demo=1 flag.
            self.assertIn("URLSearchParams", html, page)
            self.assertIn("'demo'", html, page)

    def test_credential_values_are_base64_encoded_in_source(self):
        import base64

        for page in self.PAGES:
            html = self._read(page)
            for plain in ("user@demo.com", "admin@demo.com", "Demo@1234"):
                encoded = base64.b64encode(plain.encode()).decode()
                self.assertIn(encoded, html, f"{page}: encoded form of {plain!r}")


if __name__ == "__main__":
    unittest.main()
