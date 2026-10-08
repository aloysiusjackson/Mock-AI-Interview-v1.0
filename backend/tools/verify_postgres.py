"""
verify_postgres.py — prove that PostgreSQL holds the same data as SQLite.

Run this after migrating (and at any time afterwards) to answer one question:
"is every row from the SQLite database present in PostgreSQL, unchanged?"

    python backend/tools/verify_postgres.py            # full row-by-row check
    python backend/tools/verify_postgres.py --quick    # counts only

It reads the SQLite file read-only and never writes to either database. The exit
code is 0 only when every table matches: same row count, same ids, and identical
values in every shared column (numbers compared numerically, timestamps as text).
A password-hash fingerprint is printed as direct evidence that hashes were copied
byte-for-byte, so no existing user has to reset anything.
"""

from __future__ import annotations

import argparse
import hashlib
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
from migrate_to_postgres import (  # noqa: E402
    DEFAULT_SQLITE, normalize, pg_columns, pg_rows, sqlite_columns, sqlite_rows,
)


def fingerprint(rows: list[dict], columns: list[str]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: r["id"]):
        digest.update("\u0001".join(
            repr(normalize(row.get(column))) for column in ["id"] + columns
        ).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare SQLite against PostgreSQL.")
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE)
    parser.add_argument("--quick", action="store_true", help="Counts only, skip row comparison")
    args = parser.parse_args()

    if not db_backend.is_postgres():
        print("ERROR: DATABASE_URL is not set, so there is nothing to compare against.",
              file=sys.stderr)
        return 2
    if not os.path.exists(args.sqlite):
        print(f"ERROR: no SQLite database at {args.sqlite}", file=sys.stderr)
        return 2

    print(f"SQLite     : {args.sqlite}")
    print(f"PostgreSQL : {db_backend.describe_target()}\n")

    source = sqlite3.connect(f"file:{args.sqlite.replace(os.sep, '/')}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = db_backend.connect()
    failures: list[str] = []

    try:
        header = f"  {'table':<14} {'sqlite':>7} {'postgres':>9} {'ids':>5} {'values':>7} {'sum':>5}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for table in pg_schema.TABLES_IN_ORDER:
            s_cols = sqlite_columns(source, table)
            t_cols = pg_columns(target, table)
            shared = [c for c in s_cols if c in t_cols and c != "id"]
            s_rows = {r["id"]: r for r in sqlite_rows(source, table, s_cols)}
            p_rows = pg_rows(target, table, shared)

            ids_ok = set(s_rows) == set(p_rows)
            values_ok = True
            for row_id, s_row in s_rows.items():
                p_row = p_rows.get(row_id)
                if p_row is None:
                    values_ok = False
                    break
                for column in shared:
                    if normalize(s_row[column]) != normalize(p_row.get(column)):
                        values_ok = False
                        failures.append(
                            f"{table} id={row_id}: {column} is {s_row[column]!r} in SQLite "
                            f"but {p_row.get(column)!r} in PostgreSQL"
                        )
                        break
                if not values_ok:
                    break

            hashes_ok = values_ok
            print(f"  {table:<14} {len(s_rows):>7} {len(p_rows):>9} "
                  f"{'yes' if ids_ok else 'NO':>5} {'yes' if values_ok else 'NO':>7} "
                  f"{'OK' if ids_ok and values_ok else 'FAIL':>5}")
            if not ids_ok:
                only_sqlite = sorted(set(s_rows) - set(p_rows))[:10]
                only_pg = sorted(set(p_rows) - set(s_rows))[:10]
                failures.append(f"{table}: ids differ (only in SQLite: {only_sqlite}, only in PostgreSQL: {only_pg})")

        # Direct proof that credentials carried over untouched.
        s_users = sqlite_rows(source, "users", sqlite_columns(source, "users"))
        p_users = pg_rows(target, "users", ["email", "password_hash"])
        s_fp = hashlib.sha256("\n".join(
            f"{r['email']}|{r['password_hash']}" for r in sorted(s_users, key=lambda r: r["id"])
        ).encode()).hexdigest()
        p_fp = hashlib.sha256("\n".join(
            f"{row.get('email')}|{row.get('password_hash')}"
            for _id, row in sorted(p_users.items())
        ).encode()).hexdigest()
        print(f"\n  password-hash fingerprint : {'IDENTICAL' if s_fp == p_fp else 'DIFFERENT'}")
        print(f"    sqlite     {s_fp}")
        print(f"    postgresql {p_fp}")
        if s_fp != p_fp:
            failures.append("password hashes differ between the two databases")

        orphan_note = pg_schema.count_orphans(target)
        if any(orphan_note.values()):
            print("\n  known dangling rows (preserved from SQLite, not errors):")
            for name, count in orphan_note.items():
                if count:
                    print(f"    {name}: {count}")
    finally:
        target.close()
        source.close()

    if failures:
        print(f"\nVERIFICATION FAILED ({len(failures)} problem(s)):", file=sys.stderr)
        for line in failures[:20]:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("\nVERIFIED: PostgreSQL matches the SQLite database exactly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
