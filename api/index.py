"""api/index.py — Vercel serverless entrypoint.

This file used to carry its **own second copy** of the API, written before the
security work and never updated with it. That copy had:

* ``CORS(app)`` — every origin allowed, with credentials;
* no authentication at all — ``/api/dashboard?user_id=1`` returned any account's
  metrics, ``/api/submit-interview`` filed an interview under whatever
  ``user_id`` the caller sent, and ``/api/interview/<id>`` returned any user's
  transcript to anyone who guessed an id;
* ``jsonify({"error": str(e)})`` on every failure — driver and connection
  errors echoed straight back to the browser.

Maintaining two divergent APIs is how that happened, so the duplicate is gone.
The serverless function now serves the one hardened application in
``backend/app.py``: same session identity, same owner-scoped reads, same rate
limits, same security headers, same generic error messages, and the same
engine-agnostic database layer (SQLite locally, PostgreSQL/Supabase as soon as
``DATABASE_URL`` is set).

The Vercel project keeps its shape because ``vercel.json`` still routes
``/api/*`` here and serves ``frontend/**`` statically.
"""

import os
import sys

# The shared application lives in backend/, which is not on the path inside the
# serverless bundle.
_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app import app  # noqa: E402  (import must follow the sys.path setup)

# Vercel's Python runtime accepts either name.
handler = app
application = app
