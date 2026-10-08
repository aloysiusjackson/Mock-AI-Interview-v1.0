"""
backup_sqlite.py — take a verified, consistent backup of the live SQLite database.

Run this *before* migrating to PostgreSQL and keep the output directory until the
PostgreSQL deployment has been confirmed working. It never modifies, moves or
deletes the original database: it only reads it and writes copies.

    python backend/tools/backup_sqlite.py            # backup into backups/
    python backend/tools/backup_sqlite.py --out D:\\safety

What it produces, per run, inside ``backups/sqlite-<timestamp>/``:

* ``mock_interview.db``    — a *consistent* snapshot taken with SQLite's online
  backup API, so it is safe even while the Flask server is running.
* ``raw-*.db`` / ``raw-*.db-wal`` / ``raw-*.db-shm`` — byte-for-byte copies of
  the live files, for maximum fidelity / forensics.
* ``MANIFEST.json``        — sha256 of both the live and the backed-up file,
  per-table row counts, max ids, user ids/emails, a password-hash fingerprint
  and SQLite's own integrity check result, so the backup can be *proved*
  equivalent to the original later.

Exit code is 0 only when the snapshot's integrity check passes and every table
count matches the live database.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
DEFAULT_DB = os.path.join(BACKEND_DIR, "mock_interview.db")
TABLES = ["users", "questions", "interviews", "answers", "activity_logs", "feedback"]


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def facts(path: str) -> dict:
    """Everything we can later compare the backup against."""
    conn = sqlite3.connect(f"file:{path.replace(os.sep, '/')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        out = {
            "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "counts": {},
            "max_ids": {},
        }
        for table in TABLES:
            try:
                out["counts"][table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                out["max_ids"][table] = conn.execute(
                    f"SELECT COALESCE(MAX(id), 0) FROM {table}"
                ).fetchone()[0]
            except sqlite3.Error as exc:
                out["counts"][table] = f"unavailable ({exc})"
        users = conn.execute("SELECT id, email, password_hash FROM users ORDER BY id").fetchall()
        out["user_ids"] = [row["id"] for row in users]
        out["user_emails"] = [row["email"] for row in users]
        # Proves later that password hashes travelled byte-identical.
        out["password_hash_fingerprint"] = hashlib.sha256(
            "\n".join(f"{r['email']}|{r['password_hash']}" for r in users).encode()
        ).hexdigest()
        return out
    finally:
        conn.close()


def backup(db_path: str, out_dir: str) -> dict:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(out_dir, f"sqlite-{stamp}")
    os.makedirs(target, exist_ok=True)

    # 1. Consistent online snapshot (works while the server holds the DB open).
    snapshot = os.path.join(target, "mock_interview.db")
    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(snapshot)
    try:
        with destination:
            source.backup(destination)
    finally:
        source.close()
        destination.close()

    # 2. Raw byte copies of every live artefact (maximum fidelity).
    for path in [db_path] + sorted(glob.glob(db_path + "-*")) + [
        os.path.join(BACKEND_DIR, "interview.db")
    ]:
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(target, "raw-" + os.path.basename(path)))

    live, copy = facts(db_path), facts(snapshot)
    manifest = {
        "created": stamp,
        "source_db": db_path,
        "backup_dir": os.path.abspath(target),
        "sha256_live": sha256_of(db_path),
        "sha256_backup": sha256_of(snapshot),
        "identical_bytes": sha256_of(db_path) == sha256_of(snapshot),
        "live": live,
        "backup": copy,
        "verified_match": live == copy,
    }
    with open(os.path.join(target, "MANIFEST.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Verified SQLite backup (read-only).")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite file to back up")
    parser.add_argument(
        "--out", default=os.path.join(PROJECT_ROOT, "backups"), help="Output directory"
    )
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: no SQLite database at {args.db}", file=sys.stderr)
        return 2
    if not os.path.getsize(args.db):
        print(f"ERROR: {args.db} is empty — refusing to report success.", file=sys.stderr)
        return 2
    frontend_dir = os.path.join(PROJECT_ROOT, "frontend") + os.sep
    if os.path.abspath(args.out).startswith(frontend_dir):
        print("ERROR: refusing to write a backup inside the publicly served frontend/.",
              file=sys.stderr)
        return 2

    manifest = backup(args.db, args.out)
    live = manifest["live"]
    print(f"Backup written : {manifest['backup_dir']}")
    print(f"Integrity      : {live['integrity_check']}")
    print(f"Snapshot match : {manifest['verified_match']} (sha256 identical: {manifest['identical_bytes']})")
    print("Table               live  backup")
    for table in TABLES:
        print(f"  {table:<18} {str(live['counts'][table]):>4}  {str(manifest['backup']['counts'][table]):>6}")
    print(f"Users          : {live['user_emails']} ids {live['user_ids']}")
    print(f"Password-hash fingerprint: {live['password_hash_fingerprint'][:40]}…")
    print("Original SQLite database was NOT modified or moved.")

    ok = live["integrity_check"] == "ok" and manifest["verified_match"]
    if not ok:
        print("ERROR: backup verification failed — do not proceed with the migration.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
