"""
db_backend.py — engine compatibility layer for the Mock AI Interview database.

The application was written against SQLite (``sqlite3``, ``?`` placeholders,
``cur.lastrowid``, dict-like ``sqlite3.Row`` values). This module keeps every one
of those call sites working unchanged when the project runs on PostgreSQL
(Supabase) instead — which is what makes the SQLite → PostgreSQL migration a
configuration change rather than a rewrite.

Design rules:

* **SQLite stays the default.** With no ``DATABASE_URL`` set the code path is
  byte-for-byte the old one, so local development and the existing test suite
  are untouched.
* **PostgreSQL is opt-in via ``DATABASE_URL``.** No connection string, password
  or other credential is ever written in source — it is read from the
  environment on every call.
* **Only the SQL the project actually uses is translated** (``?`` placeholders,
  ``datetime('now', '-N unit')``, ``date(col)``, ``INTEGER PRIMARY KEY
  AUTOINCREMENT`` DDL), and the translation is deliberately conservative: any
  construct it does not recognise is passed through untouched so PostgreSQL
  raises a clear error instead of silently changing behaviour.

Nothing here imports Flask, so it stays trivially unit-testable.
"""

from __future__ import annotations

import datetime as _datetime
import decimal
import os
import re
import sqlite3

# ══════════════════════════════════════════════════════════════════════════
#  1. DSN HANDLING
# ══════════════════════════════════════════════════════════════════════════

POSTGRES_SCHEMES = ("postgres://", "postgresql://")

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "host.docker.internal")


def database_url() -> str:
    """The configured PostgreSQL DSN, or "" when running on SQLite."""
    return (os.environ.get("DATABASE_URL") or "").strip()


def is_postgres() -> bool:
    """True when DATABASE_URL points at PostgreSQL."""
    return database_url().lower().startswith(POSTGRES_SCHEMES)


def normalize_database_url(url: str | None = None) -> str:
    """Make a DSN safe for psycopg2.

    * ``postgres://`` (the scheme some hosts hand out) becomes ``postgresql://``.
    * TLS is requested for remote hosts, because Supabase rejects plaintext
      connections. Local servers keep their own default so the same code can be
      pointed at a throwaway PostgreSQL during testing.
    * A DSN that already states ``sslmode`` is never touched.
    """
    url = (url if url is not None else database_url()).strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url.lower().startswith("postgresql://"):
        return url
    if "sslmode=" in url.lower():
        return url

    without_params = url.split("?", 1)[0]
    host = ""
    if "@" in without_params:
        hostpart = without_params.rsplit("@", 1)[1]
        host = hostpart.split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
    if host and host.lower() not in _LOCAL_HOSTS:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def describe_target() -> str:
    """A log-safe description of the active database (never contains a password)."""
    url = database_url()
    if not url.lower().startswith(POSTGRES_SCHEMES):
        return "SQLite"
    # Drop any credentials before the host so this is always safe to print.
    host_and_db = url.split("?", 1)[0].rsplit("@", 1)[-1]
    return f"PostgreSQL ({host_and_db})"


# ══════════════════════════════════════════════════════════════════════════
#  2. SQL DIALECT TRANSLATION
# ══════════════════════════════════════════════════════════════════════════

# datetime('now')  -> (now() AT TIME ZONE 'utc')
_NOW_PLAIN = re.compile(r"datetime\(\s*'now'\s*\)", re.I)
# datetime('now', '-7 days') -> (now() AT TIME ZONE 'utc') - INTERVAL '7 days'
_NOW_OFFSET = re.compile(
    r"datetime\(\s*'now'\s*,\s*'([+-]?)\s*(\d+)\s+(day|days|hour|hours|minute|minutes"
    r"|month|months|year|years|second|seconds)'\s*\)",
    re.I,
)
# date(col) -> CAST(col AS DATE)   (the admin dashboard's "today" counters)
_DATE_FN = re.compile(r"\bdate\(\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)\s*\)", re.I)
# SQLite DDL: INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL PRIMARY KEY
_AUTOINCREMENT = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.I)
# Keep stored timestamps as naive UTC, exactly like SQLite's CURRENT_TIMESTAMP.
_DEFAULT_TIMESTAMP = re.compile(r"DEFAULT\s+CURRENT_TIMESTAMP", re.I)

_INSERT_INTO = re.compile(r"^\s*INSERT\s+INTO\s+", re.I)


def translate_sql(sql: str) -> str:
    """Rewrite the SQLite-only constructs the project actually uses.

    Called for PostgreSQL only. Constructs that are not recognised are left
    alone so a genuine incompatibility surfaces loudly rather than silently.
    """
    sql = _NOW_OFFSET.sub(
        lambda m: "(now() AT TIME ZONE 'utc') {sign} INTERVAL '{n} {unit}'".format(
            sign="-" if m.group(1) == "-" else "+",
            n=m.group(2),
            unit=m.group(3).rstrip("s") + "s",
        ),
        sql,
    )
    sql = _NOW_PLAIN.sub("(now() AT TIME ZONE 'utc')", sql)
    sql = _DATE_FN.sub(lambda m: f"CAST({m.group(1)} AS DATE)", sql)
    sql = _AUTOINCREMENT.sub("SERIAL PRIMARY KEY", sql)
    sql = _DEFAULT_TIMESTAMP.sub("DEFAULT (now() AT TIME ZONE 'utc')", sql)
    return sql


def bind_placeholders(sql: str, has_params: bool) -> str:
    """Turn SQLite ``?`` placeholders into psycopg2 ``%s``.

    A literal ``%`` (for example inside ``LIKE '%foo%'``) must be doubled when
    parameters are supplied, or psycopg2 would try to interpolate it. Both
    rewrites happen in a single pass over the original string.
    """
    if not has_params:
        return sql
    out: list[str] = []
    for char in sql:
        if char == "?":
            out.append("%s")
        elif char == "%":
            out.append("%%")
        else:
            out.append(char)
    return "".join(out)


def with_returning_id(sql: str) -> str:
    """Append ``RETURNING id`` to an INSERT so ``cursor.lastrowid`` can be filled.

    Every table in this project has an ``id`` column and every INSERT statement
    targets one of those tables, which is what makes this safe as a blanket rule.
    Statements that already carry a RETURNING clause are left untouched.
    """
    if not _INSERT_INTO.match(sql):
        return sql
    if re.search(r"\breturning\b", sql, re.I):
        return sql
    return sql.rstrip().rstrip(";").rstrip() + " RETURNING id"


# ══════════════════════════════════════════════════════════════════════════
#  3. ROW VALUE CONVERSION
# ══════════════════════════════════════════════════════════════════════════

def _to_json_friendly(value):
    """Match SQLite's value shapes so API responses do not change.

    SQLite returns timestamps as ``'YYYY-MM-DD HH:MM:SS'`` text and REAL
    columns as floats. PostgreSQL would hand back ``datetime``/``Decimal``
    objects, which Flask would serialise differently (RFC-822 dates) or refuse
    to serialise at all. Converting here keeps every JSON response identical.
    """
    if isinstance(value, _datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, _datetime.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def convert_row(row):
    """Return a plain dict with SQLite-shaped values."""
    if row is None:
        return None
    return {key: _to_json_friendly(value) for key, value in dict(row).items()}


# ══════════════════════════════════════════════════════════════════════════
#  4. POSTGRESQL CONNECTION / CURSOR WRAPPERS
# ══════════════════════════════════════════════════════════════════════════

class PgCursor:
    """A psycopg2 cursor that behaves like the ``sqlite3`` cursors used here.

    * returns dict rows (``sqlite3.Row`` equivalent)
    * exposes ``lastrowid`` after an INSERT
    * translates ``?`` placeholders and the SQLite-only SQL listed above
    * rolls the connection back on error so a caught failure leaves the
      connection usable, the way SQLite behaved
    """

    def __init__(self, connection, dict_rows: bool = True):
        from psycopg2.extras import RealDictCursor

        self._connection = connection
        self._cursor = connection.cursor(
            cursor_factory=RealDictCursor if dict_rows else None
        )
        self.lastrowid = None
        self._dict_rows = dict_rows

    # -- execution ---------------------------------------------------------
    def execute(self, sql, params=None):
        translated = bind_placeholders(translate_sql(sql), bool(params))
        try:
            if params:
                self._cursor.execute(with_returning_id(translated), tuple(params))
            else:
                self._cursor.execute(with_returning_id(translated))
        except Exception:
            # PostgreSQL aborts the whole transaction on error; roll back so the
            # caller can keep using the connection (SQLite allowed that).
            try:
                self._connection.rollback()
            except Exception:
                pass
            raise

        if _INSERT_INTO.match(sql):
            row = None
            try:
                row = self._cursor.fetchone()
            except Exception:
                row = None
            if row is not None:
                self.lastrowid = dict(row).get("id")
        return self

    def executemany(self, sql, seq_of_params):
        self._cursor.executemany(
            bind_placeholders(translate_sql(sql), True), list(seq_of_params)
        )
        return self

    # -- reading -----------------------------------------------------------
    def _shape(self, row):
        return convert_row(row) if self._dict_rows else row

    def fetchone(self):
        return self._shape(self._cursor.fetchone())

    def fetchall(self):
        return [self._shape(row) for row in self._cursor.fetchall()]

    def fetchmany(self, size=None):
        rows = self._cursor.fetchmany(size) if size else self._cursor.fetchmany()
        return [self._shape(row) for row in rows]

    def __iter__(self):
        for row in self._cursor:
            yield self._shape(row)

    # -- misc --------------------------------------------------------------
    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        self._cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


class PgConnection:
    """``sqlite3.Connection``-shaped wrapper around a psycopg2 connection."""

    def __init__(self, dsn: str):
        import psycopg2

        self._connection = psycopg2.connect(dsn)

    def cursor(self, *_args, **_kwargs):
        return PgCursor(self._connection)

    def execute(self, sql, params=None):
        """Shortcut used by security.py: ``conn.execute(...).fetchone()``."""
        cursor = self.cursor()
        return cursor.execute(sql, params)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()

    # -- helpers used by the schema/init code ------------------------------
    def raw_cursor(self, dict_rows: bool = False):
        """A cursor that skips dialect translation (used to run DDL verbatim)."""
        from psycopg2.extras import RealDictCursor

        return self._connection.cursor(
            cursor_factory=RealDictCursor if dict_rows else None
        )


def connect(url: str | None = None):
    """Open a PostgreSQL connection. Raises a clear error when psycopg2 is absent."""
    try:
        import psycopg2  # noqa: F401
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "DATABASE_URL is set but psycopg2 is not installed. "
            "Install it with: pip install psycopg2-binary"
        ) from exc
    return PgConnection(normalize_database_url(url))


def connect_sqlite(db_path: str):
    """The original SQLite connection, unchanged."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
