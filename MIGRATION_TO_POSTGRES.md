# Moving the database from SQLite to Supabase PostgreSQL

The app runs on **SQLite** by default and on **PostgreSQL** as soon as the
`DATABASE_URL` environment variable is set. Nothing in the frontend, the API
contract or the authentication flow changes — the same routes, the same JSON and
the same 199-test suite run on both engines.

```
SQLite (backend/mock_interview.db)
    ↓  backup_sqlite.py        (verified copy, read-only on the original)
    ↓  migrate_to_postgres.py  (row-preserving copy into PostgreSQL)
Supabase PostgreSQL  ← DATABASE_URL
    ↓  verify_postgres.py      (row-by-row proof that nothing was lost)
```

Existing users keep their accounts, ids, history and password hashes. Nobody is
asked to register or reset anything, and the SQLite database is never modified,
so it stays available as a rollback source.

---

## 1. What changed in the project

| File | Why |
|---|---|
| `backend/db_backend.py` | **New.** Engine compatibility layer: reads `DATABASE_URL`, translates the SQLite-only SQL the app uses (`?` placeholders, `datetime('now', '-7 days')`, `date(col)`, `INTEGER PRIMARY KEY AUTOINCREMENT`) and returns SQLite-shaped rows/`lastrowid` from PostgreSQL. No connection string is ever hardcoded. |
| `backend/pg_schema.py` | **New.** The PostgreSQL schema mirroring the SQLite schema (tables, identity columns, indexes, relationships as `NOT VALID` + validated) plus the `ROUND(double precision, int)` shim the admin dashboard needs. |
| `backend/database.py` | `get_db_connection()` now returns SQLite *or* PostgreSQL; `init_postgres_db()` creates/seeds the PostgreSQL schema; demo logins are no longer recreated on production PostgreSQL; the demo-history seeder no longer hardcodes question ids (that produced the orphaned rows noted below). |
| `backend/app.py` | Verification/reset links are only returned in API responses where that is safe (local development), so a public deployment cannot have its passwords reset by anyone who knows an email address. |
| `backend/tests/support.py` | Optional `TEST_DATABASE_URL` runs the *same* suite against PostgreSQL. |
| `backend/tools/*.py` | **New.** `backup_sqlite.py`, `migrate_to_postgres.py`, `verify_postgres.py`. |
| `.env` / `.env.example` | `DATABASE_URL` + `SECRET_KEY` placeholders and the `SEED_DEMO_ACCOUNTS` / `DEV_AUTH_LINKS` / `COOKIE_SECURE` switches. |
| `.gitignore` | `*.sqlite`, `*.sqlite3`, `*.db-wal`, `*.db-shm`, `.flask_secret`, `.env.*`, `backups/`, `.venv/`, `venv/`, logs, caches. |
| `render.yaml` | Production start command is now gunicorn (not the Flask dev server), the dead managed-Postgres block is gone, and `DATABASE_URL` / `SECRET_KEY` / SMTP / Google / Gemini values are supplied as environment variables (`sync: false` = set in the dashboard). |

No frontend file, stylesheet, image or API route was touched.

---

## 2. Back up first (always)

```bash
python backend/tools/backup_sqlite.py
```

Writes `backups/sqlite-<timestamp>/` containing a consistent snapshot
(`mock_interview.db`, taken with SQLite's online backup API while the server is
running), byte-for-byte copies of the live files, and `MANIFEST.json` with
sha256 hashes, row counts, max ids and a password-hash fingerprint. Exit code 0
means the copy passed `PRAGMA integrity_check` and matches the live database.

Keep that directory until the PostgreSQL deployment has been confirmed working.

---

## 3. Manual steps in Supabase

1. Create a project at <https://supabase.com> (choose the region closest to your
   users) and wait for it to finish provisioning.
2. Open **Project Settings → Database → Connection string → URI**.
3. Copy the URI and replace `[YOUR-PASSWORD]` with your database password
   (the one you chose when creating the project). It looks like:

   ```
   postgresql://postgres.abcdefghijklm:YOUR-PASSWORD@aws-0-eu-west-1.pooler.supabase.com:5432/postgres
   ```

4. Nothing else is required: the tables, indexes, foreign keys and the question
   bank are created automatically by the migration (or on first app start).
   You do **not** need to run any SQL by hand, and you should not create a
   different schema — the app uses the `public` schema that Supabase provides.
5. Optional hardening: **Project Settings → Database** → confirm SSL is enforced
   (the app requests `sslmode=require` for remote hosts automatically) and keep
   the connection pooler URI for serverless/short-lived connections.

> If your password contains characters such as `@`, `:` or `/`, URL-encode them
> in the URI (`@` → `%40`) or the connection will be rejected.

---

## 4. Point the project at Supabase

Local `.env` (already contains an empty `DATABASE_URL=` line to fill in):

```bash
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Nothing else changes. `load_dotenv()` reads it, `db_backend.is_postgres()` turns
true, and every existing query runs against PostgreSQL.

---

## 5. Migrate and verify

```bash
# 1. see exactly what would move (writes nothing)
python backend/tools/migrate_to_postgres.py --dry-run

# 2. copy the data, preserving ids, timestamps and password hashes
python backend/tools/migrate_to_postgres.py

# 3. prove it (row by row), at any time
python backend/tools/verify_postgres.py
```

`migrate_to_postgres.py` prints one line per table and exits non-zero unless
every row is present with identical values. It is idempotent: rows that are
already there are skipped, so an interrupted run can simply be repeated. It only
ever INSERTs — `--replace` (which truncates the target first) is required to
overwrite existing PostgreSQL data, and nothing is ever written to SQLite.

A JSON report is written to `backups/migration-report-<timestamp>.json`.

---

## 6. Run the app on PostgreSQL

```bash
# local smoke test on a different port, leaving the SQLite one running
DATABASE_URL="postgresql://…" PORT=5001 python backend/app.py
```

Production (Render): set `DATABASE_URL` in the dashboard (it is declared
`sync: false` in `render.yaml`), plus `SMTP_EMAIL`, `SMTP_PASSWORD`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and optionally `GEMINI_API_KEY`.
`SECRET_KEY` is generated by the blueprint, `COOKIE_SECURE=1` is preset, and the
start command is:

```
gunicorn --chdir backend app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60
```

Manual steps in the hosting platform:

1. **Environment → add `DATABASE_URL`** (the Supabase URI) and the SMTP / Google
   values. Never commit them.
2. Confirm the start command is the gunicorn one above (the blueprint sets it).
3. Deploy, then hit `https://<your-app>/api/roles` — it should return the 17
   roles read from Supabase.
4. Because Render's own filesystem is ephemeral, PostgreSQL is now the only
   durable store: **do not** attach a persistent disk for the SQLite file and do
   not rely on the old `mock_interview.db` in production.

---

## 7. Google OAuth production URLs

The OAuth client is configured in the Google Cloud console, not in the code. Add
the production values under **APIs & Services → Credentials → your OAuth 2.0
Client ID**:

* Authorized JavaScript origins: `https://<your-app-domain>`
* Authorized redirect URIs: `https://<your-app-domain>/login.html`

The app derives the redirect URI from the page it is served from, so it requests
exactly `https://<your-app-domain>/login.html`; the mismatch
(`redirect_uri_mismatch`) can only be fixed in that console screen. Leave the
existing `http://localhost:5000` / `http://127.0.0.1:5000` entries in place for
local development.

Gmail SMTP needs an App Password (Google account → 2-Step Verification → App
passwords → Mail). Without `SMTP_EMAIL`/`SMTP_PASSWORD` the app still works, but
verification and reset emails are not sent — and because returning those links in
an API response is unsafe on a public site, they are suppressed in production.

---

## 8. Notes and known data quirks

* **Rows with dangling references are preserved.** The SQLite database contains
  7 `answers` rows whose `question_id` points at questions deleted long ago
  (SQLite declared that relationship but never enforced it, and the demo-history
  seeder used to hardcode ids 1/2/4/5 — that seeder is fixed now). They were
  copied over unchanged; `answers.question_id` is deliberately left unenforced
  because the interview room also submits answers for AI-generated questions
  that are never written to the question bank. The other relationships *are*
  enforced and were validated during the migration. Record counts:
  `python backend/tools/verify_postgres.py` prints them.
* **Demo logins are off on PostgreSQL.** `admin@demo.com` / `user@demo.com` are
  created for local SQLite development only, so a public deployment has no
  account with a published password. Create your real admin with
  `python backend/tests/create_admin.py you@example.com "Your Name"`.
  (Set `SEED_DEMO_ACCOUNTS=1` if you deliberately want them back.)
* **Sessions** are signed with `SECRET_KEY`; set it in the environment, otherwise
  a key file is generated in the container and everyone is logged out on redeploy.
* Timestamps stay naive UTC (`YYYY-MM-DD HH:MM:SS`), exactly as SQLite stored
  them, so the API responses and the admin dashboard look unchanged.
* Boolean-ish columns (`is_verified`, `interview_blocked`, `cheat_strikes`) stay
  integers for the same reason — the API keeps returning `1`/`0`.

---

## 9. Rollback

Rolling back is configuration-only; no data has to be recreated.

1. **If PostgreSQL misbehaves:** remove/blank `DATABASE_URL` (and delete the
   variable in the host's dashboard) and restart. The app boots straight back
   onto `backend/mock_interview.db`, which was never modified.
2. **If you need the backup instead of the live file:**

   ```bash
   cp backups/sqlite-<timestamp>/mock_interview.db backend/mock_interview.db
   ```

   Stop the server first, and remember that anything written to PostgreSQL since
   the migration exists only there (re-run `migrate_to_postgres.py` the other way
   is not supported — export those rows first if you need them).
3. **In the host:** remove `DATABASE_URL` and redeploy; nothing else in the app
   depends on PostgreSQL.

The SQLite database and its verified backups stay in the project until you
confirm the PostgreSQL deployment is working.

---

## 10. Locking the Supabase tables (row-level security)

Once the data is in place, run `backend/tools/supabase_rls.sql` in the Supabase
SQL editor. It enables row-level security on `users`, `questions`, `interviews`,
`answers`, `activity_logs` and `feedback`, adds **no** policies (deny by
default), and revokes table/sequence access from the `anon` and `authenticated`
roles.

The app does not notice: it connects with the `DATABASE_URL` role, which owns the
tables, and a table owner is exempt from its own policies. What the script
removes is every *other* way into the data — a leaked publishable key, a
PostgREST query from the browser, or anyone loading supabase-js on the site.

A permissive policy such as `USING (true)` for `anon` would undo that entirely,
so none is created. If a client-side data path is ever added, scope it with
`auth.uid() = user_id` rather than opening the table.

The script is idempotent and safe to re-run after a schema change.

---

## 11. Running behind the host's proxy

Render (like most hosts) terminates TLS in front of the app and forwards the
original scheme and client address in `X-Forwarded-*` headers. Set:

```
TRUST_PROXY=1     # trust exactly one proxy hop
COOKIE_SECURE=1   # only send the session cookie over TLS
```

`TRUST_PROXY` is off by default on purpose. When it is on, the app applies
Werkzeug's `ProxyFix` with a single hop, which is what makes `request.is_secure`
true (so HSTS is sent and the session cookie is accepted), keeps
`request.host_url` on the public URL inside verification emails, and keys rate
limits on the visitor rather than on the proxy. When it is off, the same headers
are ignored — otherwise a client talking to the process directly could forge
them and choose its own rate-limit bucket.

`FORCE_HTTPS=1` is optional: it redirects plain-HTTP requests (301 for reads,
308 for writes so a POST keeps its method). Most hosts already redirect at the
edge.
