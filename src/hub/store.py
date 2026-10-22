"""The hub's Postgres: versioned SQL migrations and the small amount of state the sync and reconciliation keep."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from importlib import resources
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

LOCK_NAMESPACE = 0x0F1A  # pg_advisory_lock(namespace, key): one sync per CRM at a time


class AlreadyRunning(RuntimeError):
    pass


def migrations() -> list[tuple[str, str]]:
    files = sorted((p for p in resources.files("hub.sql").iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
    return [(p.name.removesuffix(".sql"), p.read_text(encoding="utf-8")) for p in files]


class Store:
    def __init__(self, dsn: str, max_size: int = 4) -> None:
        self.dsn = dsn
        self._pool: ConnectionPool[psycopg.Connection[dict[str, Any]]] = ConnectionPool(
            dsn, min_size=1, max_size=max_size, kwargs={"row_factory": dict_row}, open=False
        )

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        if self._pool.closed:
            self._pool.open(wait=True, timeout=10)
        with self._pool.connection() as conn:  # commits on clean exit, rolls back on error
            yield conn

    def close(self) -> None:
        self._pool.close()

    def migrate(self) -> list[str]:
        applied: list[str] = []
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
            for version, sql in migrations():
                if version in done:
                    continue
                conn.execute(sql)  # type: ignore[arg-type]  # trusted, packaged SQL
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                applied.append(version)
            conn.commit()
        return applied

    @contextmanager
    def lock(self, name: str) -> Iterator[None]:
        """A session-level advisory lock; a second sync for the same CRM fails fast instead of racing the first."""
        key = int.from_bytes(name.encode()[:4].ljust(4, b"\0"), "big") & 0x7FFFFFFF
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            got = conn.execute("SELECT pg_try_advisory_lock(%s, %s)", (LOCK_NAMESPACE, key)).fetchone()
            if not got or not got[0]:
                raise AlreadyRunning(f"another {name} run holds the lock")
            try:
                yield
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, key))

    # cursor
    def cursor(self, source: str) -> datetime | None:
        with self.connect() as conn:
            row = conn.execute("SELECT cursor_at FROM sync_cursor WHERE source = %s", (source,)).fetchone()
            return row["cursor_at"] if row else None

    def set_cursor(self, source: str, at: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sync_cursor (source, cursor_at) VALUES (%s, %s)
                   ON CONFLICT (source) DO UPDATE SET cursor_at = GREATEST(sync_cursor.cursor_at, EXCLUDED.cursor_at),
                                                      updated_at = now()""",
                (source, at),
            )
            conn.commit()

    # links and the write-back outbox, recorded together
    def link(
        self,
        crm: str,
        deal_id: str,
        invoice_id: int,
        invoice_ref: str | None,
        customer_id: int,
        amount: Decimal,
        currency: str,
        billing_status: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO deal_link (crm, deal_id, erp_invoice_id, erp_invoice_ref, erp_customer_id, amount, currency)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (crm, deal_id) DO UPDATE SET erp_invoice_id = EXCLUDED.erp_invoice_id,
                       erp_invoice_ref = EXCLUDED.erp_invoice_ref, erp_customer_id = EXCLUDED.erp_customer_id,
                       amount = EXCLUDED.amount, currency = EXCLUDED.currency, synced_at = now()""",
                (crm, deal_id, invoice_id, invoice_ref, customer_id, amount, currency),
            )
            self._queue_writeback(conn, crm, deal_id, invoice_ref, billing_status)
            conn.execute(
                "UPDATE dead_letter SET resolved_at = now() WHERE crm = %s AND deal_id = %s AND resolved_at IS NULL",
                (crm, deal_id),
            )
            conn.commit()

    def queue_writeback(
        self, crm: str, deal_id: str, invoice_ref: str | None, billing_status: str, force: bool = False
    ) -> None:
        """`force` re-sends even if the outbox already delivered these values (the CRM was changed by hand since)."""
        with self.connect() as conn:
            self._queue_writeback(conn, crm, deal_id, invoice_ref, billing_status, force)
            conn.commit()

    @staticmethod
    def _queue_writeback(
        conn: psycopg.Connection[Any], crm: str, deal_id: str, ref: str | None, status: str, force: bool = False
    ) -> None:
        only_if_changed = (
            ""
            if force
            else """WHERE crm_writeback.invoice_ref IS DISTINCT FROM EXCLUDED.invoice_ref
                  OR crm_writeback.billing_status IS DISTINCT FROM EXCLUDED.billing_status"""
        )
        conn.execute(
            """INSERT INTO crm_writeback (crm, deal_id, invoice_ref, billing_status) VALUES (%s, %s, %s, %s)
               ON CONFLICT (crm, deal_id) DO UPDATE SET invoice_ref = EXCLUDED.invoice_ref,
                   billing_status = EXCLUDED.billing_status, done_at = NULL, attempts = 0, last_error = NULL
               """
            + only_if_changed,  # type: ignore[operator]
            (crm, deal_id, ref, status),
        )

    def get_link(self, crm: str, deal_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM deal_link WHERE crm = %s AND deal_id = %s", (crm, deal_id)).fetchone()

    def links(self, crm: str) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM deal_link WHERE crm = %s", (crm,)).fetchall()
            return {r["deal_id"]: r for r in rows}

    def pending_writebacks(self, crm: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM crm_writeback WHERE crm = %s AND done_at IS NULL ORDER BY created_at LIMIT %s",
                (crm, limit),
            ).fetchall()

    def writeback_done(self, crm: str, deal_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE crm_writeback SET done_at = now() WHERE crm = %s AND deal_id = %s", (crm, deal_id))
            conn.commit()

    def writeback_failed(self, crm: str, deal_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE crm_writeback SET attempts = attempts + 1, last_error = %s WHERE crm = %s AND deal_id = %s",
                (error[:500], crm, deal_id),
            )
            conn.commit()

    # dead letters
    def dead_letter(self, crm: str, deal_id: str, reason: str, detail: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO dead_letter (crm, deal_id, reason, detail) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (crm, deal_id) WHERE resolved_at IS NULL
                   DO UPDATE SET attempts = dead_letter.attempts + 1, last_seen = now(), detail = EXCLUDED.detail,
                                 reason = EXCLUDED.reason""",
                (crm, deal_id, reason, detail[:1000]),
            )
            conn.commit()

    def open_dead_letters(self, crm: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM dead_letter WHERE crm = %s AND resolved_at IS NULL ORDER BY first_seen", (crm,)
            ).fetchall()

    # runs
    def start_run(self, crm: str) -> int:
        with self.connect() as conn:
            row = conn.execute("INSERT INTO sync_run (crm) VALUES (%s) RETURNING id", (crm,)).fetchone()
            conn.commit()
            assert row is not None
            return int(row["id"])

    def finish_run(self, run_id: int, status: str, stats: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sync_run SET finished_at = now(), status = %s, stats = %s WHERE id = %s",
                (status, json.dumps(stats, default=str), run_id),
            )
            conn.commit()

    # reconciliation runs
    def save_recon_run(self, crm: str, exceptions: list[Any], stats: dict[str, Any]) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "INSERT INTO recon_run (crm, finished_at, stats) VALUES (%s, now(), %s) RETURNING id",
                (crm, json.dumps(stats, default=str)),
            ).fetchone()
            assert row is not None
            run_id = int(row["id"])
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO recon_exception (run_id, fingerprint, kind, key, severity, summary, detail)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (run_id, e.fingerprint, e.kind, e.key, e.severity, e.summary, json.dumps(e.detail, default=str))
                        for e in exceptions
                    ],
                )
            return run_id

    # tickets
    def open_ticket_links(self, system: str) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ticket_link WHERE system = %s AND closed_at IS NULL", (system,)
            ).fetchall()
            return {r["fingerprint"]: r for r in rows}

    def save_ticket_link(
        self, system: str, fp: str, kind: str, key: str, ticket_id: str, ref: str, run_id: int
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO ticket_link (system, fingerprint, kind, key, ticket_id, ticket_ref, last_seen_run)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (system, fp, kind, key, ticket_id, ref, run_id),
            )

    def touch_ticket(self, system: str, fp: str, run_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE ticket_link SET last_seen_run = %s WHERE system = %s AND fingerprint = %s AND closed_at IS NULL",
                (run_id, system, fp),
            )

    def close_ticket_link(self, system: str, fp: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE ticket_link SET closed_at = now() WHERE system = %s AND fingerprint = %s AND closed_at IS NULL",
                (system, fp),
            )

    # webhooks
    def accept_delivery(self, digest: str, source: str, records: int) -> bool:
        """True the first time a delivery is seen; False for a replay."""
        with self.connect() as conn:
            row = conn.execute(
                """INSERT INTO webhook_delivery (digest, source, records) VALUES (%s, %s, %s)
                   ON CONFLICT (digest) DO NOTHING RETURNING digest""",
                (digest, source, records),
            ).fetchone()
            return row is not None
