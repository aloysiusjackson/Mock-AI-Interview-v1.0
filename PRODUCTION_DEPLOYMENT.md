# Production deployment guide

Everything below is what the project already supports; the only manual work left
is creating the Supabase project, creating the Render service, and pasting the
values in. Nothing in the UI, the routes or the user experience changes.

For the database-specific detail (schema, migration scripts, verification), read
[MIGRATION_TO_POSTGRES.md](MIGRATION_TO_POSTGRES.md) as well — this file is the
deployment checklist around it.

---

## 1. What is already prepared

| Area | State |
| --- | --- |
| Database | One `DATABASE_URL` switches the whole app from SQLite to PostgreSQL. Empty = local SQLite, set = PostgreSQL. |
| Passwords | PBKDF2-HMAC-SHA256, 600k iterations, per-user salt. Legacy `sha256(password+salt)` hashes still log in and are transparently upgraded to PBKDF2 on the next successful login. |
| Sessions | Signed, `HttpOnly`, `SameSite=Lax` cookie. Identity is server-side; endpoints never trust a client-supplied `user_id`. |
| Admin | Every `/api/admin/*` route is gated by `security.require_admin`, which re-reads the role from the database. Hiding a button in the browser is not what protects the panel. |
| Errors | Handlers return generic JSON; the exception text goes to the server log only. The Werkzeug debugger is off unless `FLASK_DEBUG=1` is set explicitly. |
| CORS | Same-origin by default. `ALLOWED_ORIGINS` adds explicit origins; a wildcard is never used. |
| Headers | `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy` on every response, plus `Strict-Transport-Security` over HTTPS. |
| Rate limits | Per-IP (and per-account on login) sliding windows on login, signup, password reset and the AI/resume endpoints. |
| Uploads | Resumes are parsed in memory and never written to disk, so there is no public upload directory to leak. |
| Secrets | Only in the environment. `.env` is git-ignored; the production ZIP contains `.env.example` with placeholders. |

---

## 2. Environment variables

Set these in the host's dashboard (Render → Environment). Never commit them.

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | **yes, in production** | Supabase connection string. Empty means SQLite, so production must set it. |
| `SECRET_KEY` | **yes** | Signing key for session cookies. Without it a key file is generated next to the app and every redeploy logs everyone out. |
| `COOKIE_SECURE` | **yes over HTTPS** (`1`) | Marks the session cookie `Secure`. |
| `TRUST_PROXY` | yes behind a proxy (`1`) | Trust exactly one reverse-proxy hop. Enables HSTS, correct links in emails, and per-visitor rate limiting. Leave unset when clients connect directly to the process. |
| `FORCE_HTTPS` | optional (`1`) | Redirects plain HTTP to HTTPS (301 for reads, 308 for writes). Most hosts already do this at the edge. |
| `ALLOWED_ORIGINS` | optional | Comma-separated extra origins for CORS, only when the frontend is hosted separately. |
| `GEMINI_API_KEY` | optional | Enables live AI grading; without it the built-in rule-based engine is used. |
| `SMTP_SERVER` / `SMTP_PORT` / `SMTP_EMAIL` / `SMTP_PASSWORD` | optional | Needed for real verification and password-reset emails. Use a Gmail App Password. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | optional | "Continue with Google". The client id is public; the secret is server-side only. |
| `PORT` | host-provided | Render injects it. Defaults to 5000 locally. |
| `MAX_CONTENT_LENGTH` | optional | Request-body cap, default 8 MB. |
| `SEED_DEMO_ACCOUNTS` / `DEV_AUTH_LINKS` / `FLASK_DEBUG` | leave unset | Local-development switches. They are disabled automatically as soon as `DATABASE_URL` is set. |

---

## 3. Deploy in order

1. **Supabase** — create the project, then copy the connection string from
   Project Settings → Database → Connection string → URI (with the password
   filled in). Leave the database empty; the schema is created by the app.
2. **Migrate the existing data** (see `MIGRATION_TO_POSTGRES.md` §5):
   ```bash
   python backend/tools/backup_sqlite.py
   DATABASE_URL="postgresql://..." python backend/tools/migrate_to_postgres.py
   DATABASE_URL="postgresql://..." python backend/tools/verify_postgres.py
   ```
   The verifier compares row by row and exits non-zero if anything differs.
3. **Lock the database down** — run `backend/tools/supabase_rls.sql` in the
   Supabase SQL editor. It enables row-level security with **no** permissive
   policies (deny by default) and revokes table access from the `anon` /
   `authenticated` roles. The app is unaffected because it connects as the table
   owner; only the browser-facing keys are locked out.
4. **Render** — create a Blueprint from `render.yaml`. Fill in `DATABASE_URL`,
   `SMTP_*` and `GOOGLE_*` when prompted; `SECRET_KEY` is generated for you and
   `COOKIE_SECURE`/`TRUST_PROXY` are already set.
5. **Google OAuth** — add the production URL to both lists in Google Cloud
   Console (Authorized JavaScript origin and Authorized redirect URI,
   `https://your-app.onrender.com/login.html`).
6. **Smoke test** — register, verify, log in, run one interview, open the
   dashboard, then check `/api/admin/stats` **with a normal user session**: it
   must answer `403`.

---

## 4. Rollback

Removing `DATABASE_URL` and redeploying puts the app straight back on
`backend/mock_interview.db`, which the migration never modified. Full detail,
including restoring from `backups/`, is in `MIGRATION_TO_POSTGRES.md` §9.

---

## 5. What is deliberately *not* in the production ZIP

The package is built from source only. Excluded:

* `.env` (real secrets) — `.env.example` ships instead;
* every `*.db` / `*.db-wal` / `*.db-shm` file, including the live
  `backend/mock_interview.db` that holds real user rows;
* `backups/` (local rollback copies of that database);
* `backend/.flask_secret` (a generated session key);
* `__pycache__/`, `.pytest_cache/`, `.venv/`, `node_modules/`;
* `.freebuff/` and the loose `_css_block*.txt` / `frontend/js/_*.py` build
  scripts left over from earlier editing sessions;
* any log file.
