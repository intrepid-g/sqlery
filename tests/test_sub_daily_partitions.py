"""Sub-daily partitions must be anchored at the current interval bucket (issue #78)."""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("SQLERY_TEST_PG_URL"),
        reason="SQLERY_TEST_PG_URL not set — PG required for partition tests",
    ),
]

_COVERING_SQL = (
    "SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
    "WHERE i.inhparent = 'sqlery_queued_job'::regclass "
    "AND c.relname <> 'sqlery_queued_job_default' "
    "AND pg_get_expr(c.relpartbound, c.oid) ~ 'FROM' "
    "AND (regexp_match(pg_get_expr(c.relpartbound, c.oid), 'FROM \\(''([^'']+)''\\)'))[1]"
    "::timestamptz <= now() "
    "AND (regexp_match(pg_get_expr(c.relpartbound, c.oid), 'TO \\(''([^'']+)''\\)'))[1]"
    "::timestamptz > now()"
)


def _drop_partitions(cur):
    from sqlery.core import partitioning as p

    for name, upper in p._list_partitions(cur, "sqlery_queued_job"):
        if upper is not None:
            cur.execute(f'DROP TABLE "{name}"')


@pytest.mark.django_db
def test_sub_daily_with_migration_daily_partitions_does_not_raise():
    from django.db import connection

    from sqlery.core import partitioning as p

    with connection.cursor() as cur:
        p.ensure_future_partitions(cur, "sqlery_queued_job", "1 minute", 2)
        cur.execute(_COVERING_SQL)
        assert cur.fetchone()[0] == 1


@pytest.mark.django_db
def test_sub_daily_partitions_cover_now():
    from django.db import connection

    from sqlery.core import partitioning as p

    with connection.cursor() as cur:
        _drop_partitions(cur)
        created = p.ensure_future_partitions(cur, "sqlery_queued_job", "1 minute", 2)
        assert created == 3
        cur.execute(_COVERING_SQL)
        assert cur.fetchone()[0] == 1
        # idempotent
        assert p.ensure_future_partitions(cur, "sqlery_queued_job", "1 minute", 2) == 0
