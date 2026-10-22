"""Shared fixtures. `store` is a real Postgres (Testcontainers) with the hub's migrations, fresh per test."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from hub.store import Store


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17", driver=None) as pg:
        yield pg.get_connection_url()


@pytest.fixture
def store(pg_url: str) -> Iterator[Store]:
    name = f"t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(pg_url, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    dsn = pg_url.rsplit("/", 1)[0] + f"/{name}"
    s = Store(dsn)
    s.migrate()
    yield s
    s.close()
    with psycopg.connect(pg_url, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
