"""Tests for admin panel authentication and activity logging."""

from __future__ import annotations

import unittest
import uuid

from support import ApiTestCase, query_one, execute, sent_emails, flask_app
import security


class AdminAuthTests(ApiTestCase):
    """Test that admin routes are properly protected."""

    def create_admin_user(self, name="Admin", email=None):
        """Create an admin user directly in the database."""
        if email is None:
            email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
        password = "AdminPass123!"
        user_id = execute(
            "INSERT INTO users (name, email, password_hash, role, is_verified) VALUES (?, ?, ?, 'admin', 1)",
            (name, email, security.hash_password(password))
        )
        return email, password

    def login_as_admin(self, name="Admin", email=None):
        """Create an admin user and login as them."""
        email, password = self.create_admin_user(name=name, email=email)
        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return email, password

    def test_admin_stats_requires_auth(self):
        """Unauthenticated requests to admin stats should get 401."""
        response = self.client.get("/api/admin/stats")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Authentication required", response.get_json()["error"])

    def test_admin_stats_requires_admin_role(self):
        """Non-admin authenticated users should get 403."""
        email, password = self.signup_verified(name="Regular User")
        self.login(email, password)
        response = self.client.get("/api/admin/stats")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Admin access required", response.get_json()["error"])

    def test_admin_users_requires_admin_role(self):
        """Non-admin users cannot access user list."""
        email, password = self.signup_verified(name="Regular User")
        self.login(email, password)
        response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 403)

    def test_admin_activity_requires_admin_role(self):
        """Non-admin users cannot access activity log."""
        email, password = self.signup_verified(name="Regular User")
        self.login(email, password)
        response = self.client.get("/api/admin/activity")
        self.assertEqual(response.status_code, 403)

    def test_admin_activity_types_requires_admin_role(self):
        """Non-admin users cannot access activity types."""
        email, password = self.signup_verified(name="Regular User")
        self.login(email, password)
        response = self.client.get("/api/admin/activity/types")
        self.assertEqual(response.status_code, 403)

    def test_admin_stats_returns_data_for_admin(self):
        """Admin users can access stats endpoint."""
        self.login_as_admin(name="Admin User")
        response = self.client.get("/api/admin/stats")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("stats", data)
        self.assertIn("recent_activity", data)

    def test_admin_users_returns_paginated_list(self):
        """Admin can list users with pagination."""
        # Create multiple regular users with unique emails
        created_ids = []
        for i in range(3):
            email = f"pagetest-{uuid.uuid4().hex[:8]}@example.com"
            self.signup_verified(name=f"Page User {i}", email=email)
            created_ids.append(email)

        self.login_as_admin(name="Admin")
        response = self.client.get("/api/admin/users?per_page=2")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        # Should return at most per_page users
        self.assertLessEqual(len(data["users"]), 2)
        # total_count should be at least the users we just created + admin
        self.assertGreaterEqual(data["pagination"]["total_count"], 4)
        self.assertEqual(data["pagination"]["page"], 1)
        # Verify pagination links work
        if data["pagination"]["total_pages"] > 1:
            response = self.client.get("/api/admin/users?per_page=2&page=2")
            self.assertEqual(response.status_code, 200)
            data2 = response.get_json()
            self.assertLessEqual(len(data2["users"]), 2)

    def test_admin_can_search_users(self):
        """Admin can search users by name or email."""
        email = f"searchtest-{uuid.uuid4().hex[:8]}@example.com"
        self.signup_verified(name="Searchable User", email=email)
        self.login_as_admin()

        response = self.client.get("/api/admin/users?search=Searchable")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(len(data["users"]) >= 1)
        self.assertIn("Searchable", data["users"][0]["name"])

    def test_admin_can_filter_users_by_status(self):
        """Admin can filter users by status."""
        email = f"statustest-{uuid.uuid4().hex[:8]}@example.com"
        self.signup_verified(name="Active User", email=email)
        unverified_email = f"unverified-{uuid.uuid4().hex[:8]}@example.com"
        self.signup(name="Unverified User", email=unverified_email)
        self.login_as_admin()

        # Filter active users
        response = self.client.get("/api/admin/users?status=active")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(all(u["status"] == "active" for u in data["users"]))

    def test_admin_can_filter_users_by_role(self):
        """Admin can filter users by role."""
        self.signup_verified(name="Regular User")
        self.login_as_admin(name="Admin User")

        response = self.client.get("/api/admin/users?role=admin")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(all(u["role"] == "admin" for u in data["users"]))

    def test_admin_can_get_single_user(self):
        """Admin can view individual user details."""
        email = f"targettest-{uuid.uuid4().hex[:8]}@example.com"
        self.signup_verified(name="Target User", email=email)
        target_id = query_one("SELECT id FROM users WHERE email = ?", (email,))["id"]

        self.login_as_admin()
        response = self.client.get(f"/api/admin/users/{target_id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["user"]["id"], target_id)
        self.assertEqual(data["user"]["email"], email)

    def test_admin_cannot_access_nonexistent_user(self):
        """Admin gets 404 for non-existent user."""
        self.login_as_admin()
        response = self.client.get("/api/admin/users/99999")
        self.assertEqual(response.status_code, 404)

    def test_admin_can_update_user_role(self):
        """Admin can change user role."""
        email = f"roletest-{uuid.uuid4().hex[:8]}@example.com"
        self.signup_verified(name="Regular User", email=email)
        target_id = query_one("SELECT id FROM users WHERE email = ?", (email,))["id"]

        self.login_as_admin()
        response = self.client.patch(
            f"/api/admin/users/{target_id}",
            json={"role": "admin"},
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

        # Verify the change
        user = query_one("SELECT role FROM users WHERE id = ?", (target_id,))
        self.assertEqual(user["role"], "admin")

    def test_admin_cannot_demote_self(self):
        """Admin cannot remove their own admin access."""
        admin_email, _ = self.login_as_admin(name="Self Admin")
        admin_id = query_one("SELECT id FROM users WHERE email = ?", (admin_email,))["id"]

        response = self.client.patch(
            f"/api/admin/users/{admin_id}",
            json={"role": "user"},
            content_type="application/json"
        )
        # Should fail because admin can't demote themselves
        self.assertEqual(response.status_code, 400)
        self.assertIn("own admin access", response.get_json()["error"])

    def test_admin_can_verify_user(self):
        """Admin can verify unverified users."""
        unverified_email = f"unverified-{uuid.uuid4().hex[:8]}@example.com"
        self.signup(name="Unverified User", email=unverified_email)
        target_id = query_one("SELECT id FROM users WHERE email = ?", (unverified_email,))["id"]

        self.login_as_admin()
        response = self.client.patch(
            f"/api/admin/users/{target_id}",
            json={"is_verified": True},
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

        user = query_one("SELECT is_verified FROM users WHERE id = ?", (target_id,))
        self.assertTrue(user["is_verified"])

    def test_admin_can_suspend_user(self):
        """Admin can suspend users."""
        email = f"suspendtest-{uuid.uuid4().hex[:8]}@example.com"
        self.signup_verified(name="Regular User", email=email)
        target_id = query_one("SELECT id FROM users WHERE email = ?", (email,))["id"]

        self.login_as_admin()
        response = self.client.patch(
            f"/api/admin/users/{target_id}",
            json={"role": "suspended"},
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

        user = query_one("SELECT role FROM users WHERE id = ?", (target_id,))
        self.assertEqual(user["role"], "suspended")

    def test_admin_activity_log_includes_activities(self):
        """Admin can view activity log."""
        self.login_as_admin()
        response = self.client.get("/api/admin/activity")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("activities", data)
        self.assertIn("pagination", data)

    def test_admin_activity_log_filters_by_type(self):
        """Admin can filter activity by type."""
        self.login_as_admin()
        response = self.client.get("/api/admin/activity?type=login")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(all(a["activity_type"] == "login" for a in data["activities"]))

    def test_admin_activity_log_filters_by_user(self):
        """Admin can filter activity by user."""
        email = f"activitytest-{uuid.uuid4().hex[:8]}@example.com"
        self.signup_verified(name="Activity User", email=email)
        user_id = query_one("SELECT id FROM users WHERE email = ?", (email,))["id"]

        self.login_as_admin()
        response = self.client.get(f"/api/admin/activity?user_id={user_id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(all(a["user_id"] == user_id for a in data["activities"]))


class ActivityLoggingTests(ApiTestCase):
    """Test that activity logging works correctly."""

    def test_registration_logs_activity(self):
        """User registration should create an activity log entry."""
        self.signup(name="Test User")
        self.assertEqual(len(sent_emails), 1)

        activity = query_one(
            "SELECT * FROM activity_logs WHERE activity_type = 'registration' ORDER BY id DESC LIMIT 1"
        )
        self.assertIsNotNone(activity)
        self.assertIn("registered", activity["description"])

    def test_login_logs_activity(self):
        """Successful login should create an activity log entry."""
        email, password = self.signup_verified(name="Test User")
        self.login(email, password)

        activity = query_one(
            "SELECT * FROM activity_logs WHERE activity_type = 'login' ORDER BY id DESC LIMIT 1"
        )
        self.assertIsNotNone(activity)
        self.assertEqual(activity["status"], "success")

    def test_failed_login_logs_activity(self):
        """Failed login should create a failed activity log entry."""
        email = f"failedtest-{uuid.uuid4().hex[:8]}@example.com"
        self.signup(name="Test User", email=email)
        security.LIMITER.reset()

        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "wrongpassword"}
        )
        self.assertEqual(response.status_code, 401)

        activity = query_one(
            "SELECT * FROM activity_logs WHERE activity_type = 'failed_login' ORDER BY id DESC LIMIT 1"
        )
        self.assertIsNotNone(activity)
        self.assertEqual(activity["status"], "failed")

    def test_logout_logs_activity(self):
        """Logout should create an activity log entry."""
        email, password = self.signup_verified(name="Test User")
        self.login(email, password)

        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)

        activity = query_one(
            "SELECT * FROM activity_logs WHERE activity_type = 'logout' ORDER BY id DESC LIMIT 1"
        )
        self.assertIsNotNone(activity)

    def test_interview_submission_logs_activity(self):
        """Interview submission should create an activity log entry."""
        email, password = self.signup_verified(name="Test User")
        self.login(email, password)

        # Get a valid question_id
        question_id = query_one("SELECT id FROM questions LIMIT 1")["id"]

        response = self.client.post(
            "/api/submit-interview",
            json={
                "role": "Software Engineer",
                "answers": [{
                    "question_id": question_id,
                    "question_text": "Test question",
                    "category": "Behavioral",
                    "transcript": "Test answer"
                }]
            }
        )
        self.assertEqual(response.status_code, 200)

        activity = query_one(
            "SELECT * FROM activity_logs WHERE activity_type = 'interview_submitted' ORDER BY id DESC LIMIT 1"
        )
        self.assertIsNotNone(activity)
        self.assertIn("Score:", activity["description"])


class AdminCreationTests(ApiTestCase):
    """Test admin account creation and management."""

    def create_admin_user(self, name="Admin", email=None):
        """Create an admin user directly in the database."""
        if email is None:
            email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
        password = "AdminPass123!"
        user_id = execute(
            "INSERT INTO users (name, email, password_hash, role, is_verified) VALUES (?, ?, ?, 'admin', 1)",
            (name, email, security.hash_password(password))
        )
        return email, password

    def login_as_admin(self, name="Admin", email=None):
        """Create an admin user and login as them."""
        email, password = self.create_admin_user(name=name, email=email)
        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return email, password

    def test_can_create_admin_via_sql(self):
        """Admin accounts can be created directly in the database."""
        email = f"sqladmintest-{uuid.uuid4().hex[:8]}@example.com"
        user_id = execute(
            "INSERT INTO users (name, email, password_hash, role, is_verified) VALUES (?, ?, ?, 'admin', 1)",
            ("SQL Admin", email, security.hash_password("AdminPass123!"))
        )

        user = query_one("SELECT role FROM users WHERE id = ?", (user_id,))
        self.assertEqual(user["role"], "admin")

    def test_admin_session_works(self):
        """Admin created via SQL can login and access admin routes."""
        email = f"sqladmin2-{uuid.uuid4().hex[:8]}@example.com"
        execute(
            "INSERT INTO users (name, email, password_hash, role, is_verified) VALUES (?, ?, ?, 'admin', 1)",
            ("SQL Admin", email, security.hash_password("AdminPass123!"))
        )

        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "AdminPass123!"}
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/api/admin/stats")
        self.assertEqual(response.status_code, 200)

    def test_admin_has_last_login_field(self):
        """Admin users should have last_login tracked."""
        email, password = self.login_as_admin(name="Login Test Admin")
        user = query_one("SELECT last_login FROM users WHERE email = ?", (email,))
        self.assertIsNotNone(user["last_login"])


if __name__ == "__main__":
    unittest.main()
