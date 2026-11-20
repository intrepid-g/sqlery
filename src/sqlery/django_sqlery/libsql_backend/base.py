"""EXPERIMENTAL. Django's SQLite backend with the `libsql` module as its driver (`ENGINE: sqlery.django_sqlery.libsql_backend`).

Embedded local file only. Verified with libsql 0.1.11. Ported from the sqlery website benchmark.

The vendor stays 'sqlite', so sqlery's SQLite paths (`connection.vendor == 'sqlite'`) apply unchanged. What the `libsql` module
lacks compared with the standard library's `sqlite3`, and what this wrapper does about it:

- every error is a plain `ValueError`: mapped to sqlite3's exception classes by message, which is what Django (IntegrityError ->
  sqlery's claim races) and sqlery (OperationalError) catch;
- no `create_function`: Django's own SQL functions (date extraction and the like) are not registered; sqlery's queue and the demo's
  raw SQL use none;
- no `detect_types`, no adapters: Django's field converters parse the text columns, and `datetime`/`date`/`Decimal` parameters are
  written as Django does (`adapt_datetime`);
- the module binds every Python int of 2**31 or more as a REAL, so one above 2**53 (sqlery's 18-digit job ids) is rounded on its way into
  the file while the dependency list, JSON text, keeps the exact id: such an int is bound as its decimal text, which an INTEGER column
  (and a comparison with one) converts back exactly (#149);
- the connection's `isolation_level` is fixed (autocommit; Django starts transactions explicitly with BEGIN);
- `cursor.rowcount` is a running total over the statements of one cursor (and a SELECT adds its rows), not the rows the last statement
  changed: the wrapper reports the difference a statement made (and -1 for a SELECT, as `sqlite3` does), which sqlery's job-history
  move needs (it runs an INSERT .. SELECT and a DELETE on one cursor and checks that each touched exactly one row, #140);
- no `getlimit`, which Django reads for the batch size of a `bulk_create` (sqlery's ready-queue entries are written with one, #139):
  the SQLite default of 999 bound variables is declared instead.
"""
import datetime
import decimal
import re
import sqlite3
from itertools import tee
from collections.abc import Mapping

import libsql  # needs the `libsql` extra
from django.db.backends.sqlite3 import base as sqlite_base

FORMAT_QMARK = re.compile(r"(?<!%)%s")


class Database:
    """What `BaseDatabaseWrapper.Database` is used for: the exception classes (sqlite3's) and the engine version."""

    sqlite_version_info = libsql.sqlite_version_info
    Error = sqlite3.Error
    DatabaseError = sqlite3.DatabaseError
    DataError = sqlite3.DataError
    OperationalError = sqlite3.OperationalError
    IntegrityError = sqlite3.IntegrityError
    InternalError = sqlite3.InternalError
    ProgrammingError = sqlite3.ProgrammingError
    NotSupportedError = sqlite3.NotSupportedError
    InterfaceError = sqlite3.InterfaceError
    connect = staticmethod(libsql.connect)


def translate(exc):
    """The sqlite3 exception for a libsql `ValueError` (its only error type), by what SQLite said."""
    msg = str(exc)
    if "constraint failed" in msg or "constraint violation" in msg:
        return sqlite3.IntegrityError(msg)
    if "locked" in msg or "busy" in msg:
        return sqlite3.OperationalError(msg)
    if "syntax error" in msg or "no such" in msg or "already exists" in msg or "has no column" in msg:
        return sqlite3.OperationalError(msg)
    return sqlite3.DatabaseError(msg)


EXACT_FLOAT_INT = 2**53


def adapt(value):
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > EXACT_FLOAT_INT:
        return str(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat(" ")
    if isinstance(value, (datetime.date, decimal.Decimal)):
        return value.isoformat() if isinstance(value, datetime.date) else str(value)
    if isinstance(value, memoryview):
        return bytes(value)
    return value


class Cursor:
    """A libsql cursor with the interface Django's SQLite backend expects (format-style placeholders, sqlite3 errors)."""

    def __init__(self, cursor):
        self.cursor = cursor
        self._rowcount = -1

    @property
    def rowcount(self):
        return self._rowcount

    def _total(self):
        return max(self.cursor.rowcount, 0)

    def _counted(self, query, before):
        """The rows the statement just run changed: the growth of the cursor's running total (-1 for a SELECT)."""
        self._rowcount = -1 if query.lstrip()[:6].lower() == "select" else self._total() - before

    def execute(self, query, params=None):
        before = self._total()
        try:
            if params is None:
                self.cursor.execute(query)
            else:
                named = list(params) if isinstance(params, Mapping) else None
                query = self.convert(query, named)
                params = {k: adapt(v) for k, v in params.items()} if named is not None else [adapt(v) for v in params]
                self.cursor.execute(query, params)
        except ValueError as exc:
            raise translate(exc) from exc
        self._counted(query, before)
        return self

    def executemany(self, query, param_list):
        before = self._total()
        peek, param_list = tee(iter(param_list))
        first = next(peek, None)
        named = list(first) if isinstance(first, Mapping) else None
        query = self.convert(query, named)
        try:
            self.cursor.executemany(query, [{k: adapt(v) for k, v in p.items()} if named is not None else [adapt(v) for v in p] for p in param_list])
        except ValueError as exc:
            raise translate(exc) from exc
        self._counted(query, before)
        return self

    @staticmethod
    def convert(query, named):
        if named is None:
            return FORMAT_QMARK.sub("?", query).replace("%%", "%")
        return query % {name: f":{name}" for name in named}

    def __getattr__(self, name):  # fetchone / fetchmany / fetchall / description / rowcount / lastrowid / close / arraysize
        return getattr(self.cursor, name)

    def __iter__(self):
        return iter(self.cursor.fetchall())


class DatabaseFeatures(sqlite_base.DatabaseFeatures):
    max_query_params = 999  # the libsql connection cannot be asked (`getlimit`); 999 holds for every SQLite version


class DatabaseWrapper(sqlite_base.DatabaseWrapper):
    Database = Database
    features_class = DatabaseFeatures

    def get_connection_params(self):
        params = super().get_connection_params()
        timeout = params.get("timeout", 5.0)
        return {"database": params["database"], "timeout": timeout, "isolation_level": None}

    def get_new_connection(self, conn_params):
        conn = Database.connect(**conn_params)
        conn.execute("PRAGMA foreign_keys = ON")
        for init_command in self.init_commands:
            if init_command := init_command.strip():
                conn.execute(init_command)
        return conn

    def create_cursor(self, name=None):
        return Cursor(self.connection.cursor())

    def _set_autocommit(self, autocommit):
        pass  # libsql's connection is always in autocommit; Django opens transactions with an explicit BEGIN
