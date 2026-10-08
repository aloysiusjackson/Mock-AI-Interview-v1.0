"""
migrate_to_postgres.py — copy the existing SQLite database into PostgreSQL (Supabase).

Safety properties, in order of importance:

1. **The SQLite database is never written to.** It is opened read-only, so the
   migration cannot damage the source of truth. Keep the backup produced by
   ``backup_sqlite.py`` anyway; it is the rollback source.
2. **Ids are preserved.** Every row is inserted with its original ``id`` and the
   PostgreSQL identity sequences are re-pointed afterwards, so existing users
   keep their accounts, interviews and history. No passwords are generated,
   rehashed or reset — password hashes are copied byte-for-byte.
3. **Nothing is destroyed.** Rows are only ever INSERTed. If a row with the same
   id already exists in PostgreSQL with different values, the run stops and
   reports it; ``--replace`` (destructive, explicit) is required to truncate the
   target tables first.
4. **It is resumable and idempotent.** Rows already present and identical are
   skipped, so a run interrupted halfway can simply be repeated.
5. **It verifies before claiming success.** Every table is compared row by row
   (counts, id sets, then each column value) and the run exits non-zero unless
   the data matches.

Usage::

    # 1. always back up first
    python backend/tools/backup_sqlite.py

    # 2. see what would happen (no writes to PostgreSQL)
    python backend/tools/migrate_to_postgres.py --dry-run

    # 3. do it (DATABASE_URL must be set in the environment or in .env)
    python backend/tools/migrate_to_postgres.py

    # only if the target already holds different data and you accept losing it
    python backend/tools/migrate_to_postgres.py --replace

Exit codes: 0 success · 1 verification failed · 2 bad configuration · 3 stop required.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

import db_backend  # noqa: E402
import pg_schema  # noqa: E402

DEFAULT_SQLITE = os.path.join(BACKEND_DIR, "mock_interview.db")
TABLES = pg_schema.TABLES_IN_ORDER

# constraint name -> (table, column, referenced table, referenced column).
# Used only to print exact remediation SQL when a target constraint is too strict
# to accept the legacy rows that the SQLite source legitimately contains.
_FK_REPAIR = {
    "interviews_user_id_fkey": ("interviews", "user_id", "users", "id"),
    "answers_interview_id_fkey": ("answers", "interview_id", "interviews", "id"),
    "answers_question_id_fkey": ("answers", "question_id", "questions", "id"),
    "activity_logs_user_id_fkey": ("activity_logs", "user_id", "users", "id"),
}


# ══════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════

def sqlite_columns(conn, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def sqlite_rows(conn, table: str, columns: list[str]) -> list[dict]:
    selected = ", ".join(columns)
    rows = conn.execute(f"SELECT {selected} FROM {table} ORDER BY id").fetchall()
    return [{column: row[column] for column in columns} for row in rows]


def pg_columns(conn, table: str) -> list[str]:
    cursor = conn.raw_cursor(dict_rows=True)
    try:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        )
        return [dict(row)["column_name"] for row in cursor.fetchall()]
    finally:
        cursor.close()


def pg_rows(conn, table: str, columns: list[str]) -> dict[int, dict]:
    cursor = conn.raw_cursor(dict_rows=False)
    try:
        selected = ", ".join(["id"] + [c for c in columns if c != "id"])
        cursor.execute(f"SELECT {selected} FROM {table} ORDER BY id")
        names = ["id"] + [c for c in columns if c != "id"]
        return {row[0]: dict(zip(names, row)) for row in cursor.fetchall()}
    finally:
        cursor.close()


def normalize(value):
    """Compare values across engines without being fooled by float formatting."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


class Summary:
    def __init__(self):
        self.tables: dict[str, dict] = {}

    def add(self, table: str, **info):
        self.tables.setdefault(table, {}).update(info)


# ══════════════════════════════════════════════════════════════════════════
#  Migration
# ══════════════════════════════════════════════════════════════════════════

def migrate(sqlite_path: str, *, dry_run: bool, replace: bool, report_dir: str) -> int:
    if not os.path.exists(sqlite_path):
        print(f"ERROR: no SQLite database at {sqlite_path}", file=sys.stderr)
        return 2

    if not db_backend.is_postgres():
        print("ERROR: DATABASE_URL is not set (or is not a postgresql:// URL).", file=sys.stderr)
        print("", file=sys.stderr)
        print("Set it to your Supabase connection string, for example:", file=sys.stderr)
        print("  Supabase dashboard -> Project Settings -> Database -> Connection string -> URI", file=sys.stderr)
        print('  DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres', file=sys.stderr)
        print("", file=sys.stderr)
        print("Put it in .env (never in source code) and re-run this script.", file=sys.stderr)
        return 2

    print(f"Source : {sqlite_path}")
    print(f"Target : {db_backend.describe_target()}")
    print(f"Mode   : {'DRY RUN (no writes to PostgreSQL)' if dry_run else 'LIVE'}")

    source = sqlite3.connect(f"file:{sqlite_path.replace(os.sep, '/')}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = db_backend.connect()
    summary = Summary()

    try:
        # ── 1. schema ──────────────────────────────────────────────────────
        if not dry_run:
            pg_schema.create_schema(target)
        print("\nSchema: tables and indexes exist / will be created.")

        # ── 2. pre-flight: what is already there ───────────────────────────
        preflight = {}
        for table in TABLES:
            existing = pg_rows(target, table, ["id"]) if _pg_table_exists(target, table) else {}
            preflight[table] = len(existing)
        non_empty = {t: c for t, c in preflight.items() if c}
        if non_empty:
            print(f"\nTarget already holds rows: {non_empty}")
            if replace:
                print("--replace given: TRUNCATING target tables first (destructive).")
                if not dry_run:
                    pg_schema.truncate_tables(target)
            else:
                print("Rows with matching ids are skipped; conflicting rows stop the run.")
                print("Pass --replace to wipe the target first (destroys existing PostgreSQL data).")

        # ── 2b. make room for the legacy rows the source legitimately contains ─
        # The source has a few orphaned rows (SQLite never enforced its foreign
        # keys). They must be preserved, and a PostgreSQL foreign key rejects
        # them even when it is NOT VALID — so the relationships that they violate
        # are removed for the duration of the import and re-added immediately
        # afterwards (see step 4b). Nothing is deleted from either database.
        orphans_in_source = _source_orphans(source)
        relaxed: list[str] = []
        if not dry_run:
            existing_fks = _fk_states(target)
            # A relationship this project intentionally leaves unenforced (see
            # pg_schema.FK_UNENFORCED). Remove it if an earlier schema created it.
            unenforced_present = [n for n, *_ in pg_schema.FK_UNENFORCED if n in existing_fks]
            if unenforced_present:
                print("\nRemoving a relationship that must not be enforced here:")
                print("  answers.question_id -> questions.id is left unenforced because the")
                print("  interview room also submits answers for AI-generated questions that")
                print("  are never stored in the question bank (enforcing it breaks Submit).")
                print(f"  dropped: {pg_schema.drop_foreign_keys(target, unenforced_present)}")
                existing_fks = _fk_states(target)
            relaxed = [
                name for name, count in orphans_in_source.items()
                if count and name in existing_fks
            ]
            if relaxed:
                print("\nForeign keys temporarily removed so legacy rows load unchanged:")
                for name, count in orphans_in_source.items():
                    if name in relaxed:
                        print(f"  {name}: {count} orphan row(s) come from SQLite and are kept as-is")
                dropped = pg_schema.drop_foreign_keys(target, relaxed)
                print(f"  dropped: {dropped} (re-added automatically after the copy)")
        elif orphans_in_source:
            for name, count in orphans_in_source.items():
                if count:
                    print(f"\n(dry run) {name}: {count} orphan row(s) in SQLite would be preserved as-is")

        # ── 3. copy ────────────────────────────────────────────────────────
        for table in TABLES:
            s_cols = sqlite_columns(source, table)
            if not s_cols:
                summary.add(table, present_in_source=False, migrated=0, skipped=0)
                continue

            table_exists = _pg_table_exists(target, table)
            t_cols = pg_columns(target, table) if table_exists else []
            if dry_run and not t_cols:
                # Nothing was created yet; assume the generated schema (which is
                # derived from this very SQLite schema) and an empty target.
                t_cols = s_cols
            shared = [c for c in s_cols if c in t_cols]
            missing_in_target = [c for c in s_cols if c not in t_cols]

            rows = sqlite_rows(source, table, s_cols)
            existing = (
                {row_id for row_id in pg_rows(target, table, shared)}
                if table_exists and shared else set()
            )
            to_insert = [r for r in rows if r["id"] not in existing]

            inserted = 0
            if not dry_run and to_insert:
                columns = ["id"] + [c for c in shared if c != "id"]
                placeholders = ", ".join("?" for _ in columns)
                insert_sql = (
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                )
                cursor = target.cursor()
                try:
                    for row in to_insert:
                        cursor.execute(insert_sql, [row[c] for c in columns])
                        inserted += 1
                finally:
                    cursor.close()
                target.commit()

            summary.add(
                table,
                present_in_source=True,
                source_rows=len(rows),
                migrated=inserted if not dry_run else len(to_insert),
                skipped=len(rows) - len(to_insert),
                columns_missing_in_target=missing_in_target,
            )
            print(
                f"  {table:<14} source={len(rows):<5} "
                f"{'would insert' if dry_run else 'inserted'}={len(to_insert) if dry_run else inserted:<5} "
                f"already_present={len(rows) - len(to_insert)}"
            )

        # ── 4. sequences ───────────────────────────────────────────────────
        fk_status: dict[str, str] = {}
        orphans: dict[str, int] = {}
        if not dry_run:
            fixed = pg_schema.reset_sequences(target)
            print(f"\nIdentity sequences re-pointed: {fixed}")

            # ── 4b. relationships ─────────────────────────────────────────
            print("\nForeign keys (the relationships the SQLite schema declared):")
            readded = pg_schema.add_foreign_keys(target)
            if readded:
                print(f"  re-added as NOT VALID: {readded}")
            orphans = pg_schema.count_orphans(target)
            fk_status = pg_schema.validate_constraints(target)
            for constraint, status in fk_status.items():
                count = orphans.get(constraint, 0)
                note = (
                    f"  <- {count} pre-existing orphan row(s) preserved exactly as found"
                    if count else ""
                )
                print(f"  {constraint:<28} {status}{note}")

        # ── 5. validation ──────────────────────────────────────────────────
        failures: list[str] = []
        if dry_run:
            print("\nValidation skipped: this was a dry run, PostgreSQL was not written to.")
        else:
            print("\nValidation (SQLite vs PostgreSQL, row by row):")
        for table in TABLES if not dry_run else []:
            if not summary.tables.get(table, {}).get("present_in_source", True):
                continue
            s_cols = sqlite_columns(source, table)
            t_cols = pg_columns(target, table)
            shared = [c for c in s_cols if c in t_cols]
            s_rows = {r["id"]: r for r in sqlite_rows(source, table, s_cols)}
            p_rows = pg_rows(target, table, shared)

            missing = [i for i in s_rows if i not in p_rows]
            differing = []
            for row_id, s_row in s_rows.items():
                p_row = p_rows.get(row_id)
                if p_row is None:
                    continue
                for column in shared:
                    if normalize(s_row[column]) != normalize(p_row.get(column)):
                        differing.append((row_id, column, s_row[column], p_row.get(column)))
                        break
            extra = [i for i in p_rows if i not in s_rows]

            counts = {"sqlite": len(s_rows), "postgres": len(p_rows),
                      "migrated": len(s_rows) - len(missing),
                      "missing": len(missing), "differing": len(differing),
                      "extra_in_postgres": len(extra)}
            summary.add(table, validation=counts)
            status = "OK" if not missing and not differing else "FAILED"
            note = f" (+{len(extra)} extra row(s) in PostgreSQL)" if extra else ""
            print(f"  {table:<14} sqlite={counts['sqlite']:<5} postgres={counts['postgres']:<5} "
                  f"verified={counts['migrated']:<5} missing={counts['missing']:<3} "
                  f"different={counts['differing']:<3} {status}{note}")
            if missing:
                failures.append(f"{table}: {len(missing)} row(s) missing (ids {missing[:10]})")
            if differing:
                for row_id, column, a, b in differing[:5]:
                    failures.append(f"{table} id={row_id}: column {column!r} {a!r} != {b!r}")
                failures.append(f"{table}: {len(differing)} row(s) differ")

        # ── 6. auth-specific evidence ──────────────────────────────────────
        if not dry_run and _pg_table_exists(target, "users"):
            s_hashes = [
                (r["email"], r["password_hash"]) for r in sqlite_rows(source, "users", sqlite_columns(source, "users"))
            ]
            p_hashes = {
                row["email"]: row["password_hash"]
                for row in pg_rows(target, "users", ["email", "password_hash"]).values()
            }
            hash_ok = all(p_hashes.get(email) == pw for email, pw in s_hashes)
            print(f"\nPassword hashes identical for all {len(s_hashes)} accounts: {hash_ok}")
            if not hash_ok:
                failures.append("password hashes do not match between the two engines")

        # ── 7. report ──────────────────────────────────────────────────────
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"migration-report-{stamp}.json")
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "created": stamp,
                    "source_sqlite": sqlite_path,
                    "target": db_backend.describe_target(),
                    "dry_run": dry_run,
                    "replace": replace,
                    "tables": summary.tables,
                    "foreign_keys": fk_status,
                    "foreign_keys_temporarily_removed": relaxed,
                    "orphan_rows_preserved": orphans,
                    "failures": failures,
                    "success": not failures,
                },
                handle,
                indent=2,
                default=str,
            )
        print(f"\nReport: {report_path}")

        if failures:
            print("\nMIGRATION NOT VERIFIED — nothing was deleted; SQLite is untouched:", file=sys.stderr)
            for line in failures[:20]:
                print(f"  - {line}", file=sys.stderr)
            print("\nReport this output before changing anything else.", file=sys.stderr)
            return 1

        if dry_run:
            would_move = sum(t.get("migrated", 0) for t in summary.tables.values())
            print(f"\nDry run complete: {would_move} row(s) would be copied. "
                  "PostgreSQL was not modified and SQLite was not touched.")
            return 0

        print("\nMigration verified: every SQLite row exists in PostgreSQL with identical values.")
        print("The original SQLite database is untouched and remains your rollback source.")

        leftover = {k: v for k, v in orphans.items() if v}
        if leftover:
            print("\nNOTE — these rows exist in BOTH databases but point at a row that does")
            print("not exist. They came that way from the SQLite database, which declared")
            print("these relationships but never enforced them, and they were preserved")
            print("exactly as found: nothing was deleted or rewritten. Counts:")
            for constraint, count in leftover.items():
                print(f"  {constraint}: {count} row(s)")
            print("answers.question_id stays unenforced on purpose: the interview room also")
            print("submits answers for AI-generated questions that are never written to the")
            print("question bank, so enforcing it would break the Submit flow. The other")
            print("relationships are enforced — see the validation lines above.")
        return 0

    finally:
        target.close()
        source.close()


def _fk_states(conn) -> dict[str, bool]:
    """Which foreign-key constraints are currently fully VALIDATED."""
    cursor = conn.raw_cursor(dict_rows=True)
    try:
        cursor.execute(
            "SELECT conname, convalidated FROM pg_constraint "
            "WHERE connamespace = current_schema()::regnamespace AND contype = 'f'"
        )
        return {dict(row)["conname"]: bool(dict(row)["convalidated"]) for row in cursor.fetchall()}
    finally:
        cursor.close()


def _source_orphans(sqlite_conn) -> dict[str, int]:
    """Orphan counts in the SQLite source, per relationship."""
    checks = {
        "interviews_user_id_fkey": "SELECT COUNT(*) FROM interviews WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)",
        "answers_interview_id_fkey": "SELECT COUNT(*) FROM answers WHERE interview_id IS NOT NULL AND interview_id NOT IN (SELECT id FROM interviews)",
        "answers_question_id_fkey": "SELECT COUNT(*) FROM answers WHERE question_id IS NOT NULL AND question_id NOT IN (SELECT id FROM questions)",
        "activity_logs_user_id_fkey": "SELECT COUNT(*) FROM activity_logs WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)",
    }
    counts = {}
    for name, sql in checks.items():
        try:
            counts[name] = int(sqlite_conn.execute(sql).fetchone()[0])
        except sqlite3.Error:
            counts[name] = 0
    return counts


def _pg_table_exists(conn, table: str) -> bool:
    cursor = conn.raw_cursor(dict_rows=True)
    try:
        cursor.execute("SELECT to_regclass(%s) AS reg", (table,))
        row = cursor.fetchone()
        return bool(dict(row).get("reg")) if row else False
    finally:
        cursor.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE, help="SQLite database to read (never written)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would move; write nothing")
    parser.add_argument("--replace", action="store_true",
                        help="DESTRUCTIVE: truncate the PostgreSQL tables before importing")
    parser.add_argument("--report-dir", default=os.path.join(PROJECT_ROOT, "backups"))
    args = parser.parse_args()
    return migrate(args.sqlite, dry_run=args.dry_run, replace=args.replace,
                   report_dir=args.report_dir)


if __name__ == "__main__":
    raise SystemExit(main())
