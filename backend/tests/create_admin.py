#!/usr/bin/env python3
"""
Create an admin account in the database.

Usage:
    python create_admin.py <email> [name]

Example:
    python create_admin.py admin@example.com "Site Administrator"
"""

from __future__ import annotations

import os
import sys

# Ensure we can import from the backend
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment but don't require secrets for this script
from database import get_db_connection
from security import hash_password


def create_admin(email: str, name: str = "Administrator", password: str = None) -> None:
    """Create an admin account with the given email and name."""
    conn = get_db_connection()
    try:
        # Check if user already exists
        existing = conn.execute(
            "SELECT id, role FROM users WHERE email = ?", (email.lower(),)
        ).fetchone()

        if existing:
            if existing["role"] == "admin":
                print(f"User {email} is already an admin (ID: {existing['id']})")
            else:
                # Promote existing user to admin
                conn.execute(
                    "UPDATE users SET role = 'admin' WHERE id = ?",
                    (existing["id"],)
                )
                conn.commit()
                print(f"Promoted user {email} to admin (ID: {existing['id']})")
            return

        # Generate a secure random password if not provided
        if password is None:
            import secrets
            import string
            alphabet = string.ascii_letters + string.digits
            password = ''.join(secrets.choice(alphabet) for _ in range(16))
            print(f"Generated random password: {password}")
            print("Please save this password and share it securely with the admin user.")

        # Create the admin account
        password_hash = hash_password(password)
        cursor = conn.execute(
            """INSERT INTO users (name, email, password_hash, role, is_verified, created_at)
               VALUES (?, ?, ?, 'admin', 1, datetime('now'))""",
            (name, email.lower(), password_hash)
        )
        conn.commit()
        user_id = cursor.lastrowid

        print(f"""
Admin account created successfully!

    Email:  {email}
    Name:   {name}
    ID:     {user_id}
    Role:   admin

    Password: {password}

    Next steps:
    1. Share the password securely with the admin user
    2. The admin can now log in at /login.html
    3. After login, the Admin link will appear in the sidebar
    4. Admin can access /admin.html to manage the platform
""")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    email = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "Administrator"

    create_admin(email, name)
