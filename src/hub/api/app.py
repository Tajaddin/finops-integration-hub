"""The hub's HTTP API: what n8n, the CRM's webhooks and Prometheus talk to.

| route | who calls it | auth |
|---|---|---|
| GET /health, /ready | Docker, load balancers | none |
| GET /metrics | Prometheus | none (bind it to the internal network) |
| POST /v1/sync/runs | n8n schedule, people | X-API-Key |
| POST /v1/billing-status/refresh | n8n schedule | X-API-Key |
| POST /v1/recon/runs | n8n nightly | X-API-Key |
| POST /v1/triage | n8n (inbound message) | X-API-Key |
| POST /v1/triage/{id}/decision | n8n (approval link) | X-API-Key |
| POST /webhooks/espocrm | EspoCRM | HMAC signature |

Everything that touches a system is synchronous inside a worker thread (FastAPI runs plain `def` endpoints in its
thread pool, and the two `async` ones hand each blocking call to the same pool, so a slow ERP or ticket system never
stalls the event loop); a CRM webhook is acknowledged at once and processed in the background.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from hub import log, metrics, tracing
from hub.api.security import ApiKeyCheck, WebhookSecret, verify_espocrm
from hub.config import Settings
from hub.connectors.base import CrmSource, ErpSink
from hub.store import AlreadyRunning, Store
from hub.sync.engine import SyncEngine
from hub.tickets import TicketSink

logger = log.get(__name__)
MAX_WEBHOOK_BYTES = 1_000_000


@dataclass
class Deps:
    """How the app gets its systems; tests pass fakes."""

    crm: Callable[[], CrmSource]
    erp: Callable[[], ErpSink]
    store: Store
    tickets: Callable[[], TicketSink | None]
    triage: Callable[[], Any] | None = None  # () -> TriageWorkflow, built once on first use


class WebhookRecord(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9]{1,40}$")


class TriageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    sender: EmailStr


class Decision(BaseModel):
    approved: bool
    by: str = Field(min_length=1, max_length=200)


def create_app(settings: Settings, deps: Deps | None = None) -> FastAPI:  # noqa: PLR0915  (route table)
    if deps is None:
        from functools import cache

        from hub import factory

        @cache
        def triage_workflow() -> Any:
            from hub.triage import runtime

            return runtime.workflow(settings, factory.erp(settings))

        deps = Deps(
            crm=lambda: factory.crm(settings),
            erp=lambda: factory.erp(settings),
            store=factory.store(settings, pool=settings.sync_workers + 4),
            tickets=lambda: factory.tickets(settings),
            triage=triage_workflow,
        )
    api_key = ApiKeyCheck(settings.api_key.get_secret_value())

    def caller(request: Request) -> str:
        # one bucket for the API key (n8n is one trusted caller behind one address), one per address for everyone
        # else: a made-up key on each call must not buy a fresh bucket
        return "api-key" if api_key.ok(request.headers.get("x-api-key")) else get_remote_address(request)

    limiter = Limiter(key_func=caller, default_limits=[settings.rate_limit])  # per app, see ADR 0005
    app = FastAPI(title="finops-integration-hub", version="1.0.0", docs_url="/docs", redoc_url=None)
    app.state.limiter = limiter
    app.state.deps = deps
    app.add_middleware(SlowAPIMiddleware)  # applies default_limits to the routes without a limit of their own

    @app.exception_handler(RateLimitExceeded)
    def _rate_limited(_request: Request, _exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)

    @app.middleware("http")
    async def _timing(request: Request, call_next: Any) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        metrics.HTTP_SECONDS.labels(path, request.method, str(response.status_code)).observe(
            time.perf_counter() - start
        )
        return response

    def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if not api_key.configured:
            raise HTTPException(503, "HUB_API_KEY is not set; the API refuses every call")
        if not api_key.ok(x_api_key):
            raise HTTPException(401, "missing or wrong X-API-Key")

    authed = [Depends(require_key)]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        try:
            with deps.store.connect() as conn:
                conn.execute("SELECT 1")
        except Exception as exc:  # readiness reports, it does not raise
            return JSONResponse({"status": "not ready", "database": str(exc.__class__.__name__)}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.get("/metrics")
    def prometheus() -> Response:
        try:
            metrics.DEAD_LETTERS_OPEN.set(len(deps.store.open_dead_letters(settings.crm)))
        except Exception:  # the scrape must not fail because the database blinked
            logger.warning("metrics_dead_letters_unavailable")
        try:
            metrics.TRIAGE_UNFILED.set(deps.store.unfiled_triage_runs() if settings.tickets != "none" else 0)
        except Exception:
            logger.warning("metrics_triage_unfiled_unavailable")
        return Response(metrics.exposition(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/sync/runs", dependencies=authed)
    @limiter.limit("6/minute")
    def sync_run(request: Request, full: bool = False) -> dict[str, Any]:
        engine = SyncEngine(deps.crm(), deps.erp(), deps.store, workers=settings.sync_workers)
        try:
            summary = engine.run(full=full).summary()
        except AlreadyRunning as exc:
            metrics.SYNC_RUNS.labels("skipped_running").inc()
            raise HTTPException(409, str(exc)) from exc
        except Exception:
            metrics.SYNC_RUNS.labels("failed").inc()
            raise
        metrics.record_sync(summary, "succeeded")
        metrics.SYNC_LAST_SUCCESS.set_to_current_time()
        return summary

    @app.post("/v1/billing-status/refresh", dependencies=authed)
    @limiter.limit("6/minute")
    def billing_refresh(request: Request) -> dict[str, int]:
        return SyncEngine(deps.crm(), deps.erp(), deps.store).refresh_billing_status()

    @app.post("/v1/recon/runs", dependencies=authed)
    @limiter.limit("6/minute")
    def recon_run(request: Request, tickets: bool = True) -> dict[str, Any]:
        from hub.recon.run import run_reconciliation

        stats, _ = run_reconciliation(deps.crm(), deps.erp(), deps.store, deps.tickets() if tickets else None)
        for kind, n in stats["by_kind"].items():
            metrics.RECON_EXCEPTIONS.labels(kind).set(n)
        metrics.RECON_LAST_RUN.set_to_current_time()
        for action in ("opened", "closed_resolved", "failed"):
            if n := stats.get("tickets", {}).get(action):
                metrics.TICKETS.labels(settings.tickets, action).inc(n)
        return stats

    @app.post("/v1/triage", dependencies=authed)
    @limiter.limit("120/minute")
    async def triage_message(
        request: Request, body: TriageRequest, idempotency_key: Annotated[str | None, Header(max_length=100)] = None
    ) -> dict[str, Any]:
        """Triage one inbound message, file it as a ticket with the draft reply as an internal note, and say whether
        a person must approve it first (n8n then sends the approval request). A retried call with the same
        Idempotency-Key gets the first answer back and triages nothing new; it only files the ticket, or its note,
        if the first call failed to."""
        from hub.triage.workflow import triage

        if deps.triage is None:
            raise HTTPException(503, "triage is not configured")
        seen = await run_in_threadpool(deps.store.triage_by_key, idempotency_key) if idempotency_key else None
        if seen is not None:
            ref = seen["ticket_ref"] if seen["ticket_noted_at"] else await run_in_threadpool(file_ticket, seen["id"])
            return _triage_answer(seen["id"], seen["result"], ref, replay=True)
        workflow = await run_in_threadpool(deps.triage)  # built (indexes, model clients) on first use
        langfuse = tracing.setup(settings)
        if langfuse is None:
            result = await triage(workflow, body.message, str(body.sender))
        else:  # one trace per turn; LlamaIndex's retrieval and LLM calls nest under it (OpenInference)
            from langfuse import propagate_attributes

            with (
                langfuse.start_as_current_observation(
                    name="triage", as_type="agent", input={"message": body.message}
                ) as span,
                propagate_attributes(user_id=tracing.sender_id(str(body.sender)), trace_name="triage", tags=["triage"]),
            ):
                result = await triage(workflow, body.message, str(body.sender))
                span.update(
                    output={
                        "intent": result.intent,
                        "queue": result.queue,
                        "needs_approval": result.needs_approval,
                        "reply": result.reply,
                    },
                    metadata={"path": result.path, "citations": result.citations, "replaced": result.reply_replaced},
                )
        data = result.as_dict()
        run_id = await run_in_threadpool(deps.store.save_triage, body.sender, body.message, data, idempotency_key)
        metrics.TRIAGE.labels(result.queue, "needs_approval" if result.needs_approval else "routed").inc()
        metrics.TRIAGE_SECONDS.observe(result.seconds)
        return _triage_answer(run_id, data, await run_in_threadpool(file_ticket, run_id))

    def file_ticket(run_id: int) -> str | None:
        """File the run's ticket with the draft as an internal note; the ticket's ref, or None with no ticket system.
        The run is saved first, so when the ticket system fails the request fails, and the caller's retry with the
        same Idempotency-Key files whatever is still missing (the ticket, or only its note)."""
        sink = deps.tickets()
        if sink is None:
            return None
        run = deps.store.claim_triage_ticket(run_id)
        if run is None:
            filed = deps.store.get_triage(run_id)
            if filed is not None and filed["ticket_noted_at"]:  # filed by the request this one raced
                return filed["ticket_ref"]
            raise HTTPException(409, "the ticket for this message is being filed; retry shortly")
        result = run["result"]
        try:
            ticket_id, ticket_ref = run["ticket_id"], run["ticket_ref"]
            if ticket_id is None:
                title = f"[{result['intent'] or 'unclassified'}] " + " ".join(run["message"].split())[:120]
                tags = ["triage", result["intent"] or "unclassified"]
                tags += ["awaiting-approval"] if result["needs_approval"] else []
                tags += ["suspected-injection"] if result["suspected_injection"] else []
                ticket = sink.open(
                    title, run["message"], queue=result["queue"], priority=result["priority"], tags=tags,
                    customer=run["sender"],
                )  # fmt: skip
                deps.store.set_triage_ticket(run_id, sink.name, ticket.id, ticket.ref)
                ticket_id, ticket_ref = ticket.id, ticket.ref
            note = f"Draft reply ({', '.join(result['citations']) or 'no citations'}):\n\n{result['reply']}"
            if result["invoices"]:
                note += "\n\nInvoices in the ERP: " + json.dumps(result["invoices"])
            if result["needs_approval"]:
                note += f"\n\nRefund of {result['refund_amount']} needs finance approval before anyone replies."
            sink.note(ticket_id, note)
            deps.store.triage_ticket_noted(run_id)
        except BaseException:
            deps.store.release_triage_ticket(run_id, run["ticket_filing_until"])
            raise
        return ticket_ref

    @app.post("/v1/triage/{run_id}/decision", dependencies=authed)
    def triage_decision(run_id: int, body: Decision) -> dict[str, Any]:
        run = deps.store.get_triage(run_id)
        if run is None:
            raise HTTPException(404, "no such triage run")
        decision = "approved" if body.approved else "rejected"
        if not deps.store.decide_triage(run_id, decision, body.by):
            raise HTTPException(409, "already decided, or no approval was needed")
        sink = deps.tickets()
        if sink is not None and run["ticket_id"]:
            sink.note(run["ticket_id"], f"Refund {decision} by {body.by}.")
        metrics.TRIAGE.labels(run["result"]["queue"], decision).inc()
        return {"id": run_id, "decision": decision}

    @app.post("/webhooks/espocrm", status_code=202)
    async def espocrm_webhook(
        request: Request, background: BackgroundTasks, signature: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        body = await _read_at_most(request, MAX_WEBHOOK_BYTES)
        if body is None:
            metrics.WEBHOOKS.labels("espocrm", "too_large").inc()
            raise HTTPException(413, "payload too large")
        # read per delivery: the bootstrap writes (and rotates) the secret while the hub is running
        webhook_secret = WebhookSecret.load(settings.espocrm_webhook_secret_file)
        if webhook_secret is None or not verify_espocrm(signature, body, webhook_secret):
            metrics.WEBHOOKS.labels("espocrm", "bad_signature").inc()
            raise HTTPException(401, "bad or missing signature")
        try:
            records = [WebhookRecord.model_validate(r) for r in json.loads(body)]
        except (ValueError, TypeError) as exc:
            metrics.WEBHOOKS.labels("espocrm", "malformed").inc()
            raise HTTPException(422, "expected a JSON array of records with an id") from exc
        if not await run_in_threadpool(
            deps.store.accept_delivery, hashlib.sha256(body).hexdigest(), "espocrm", len(records)
        ):
            metrics.WEBHOOKS.labels("espocrm", "replay").inc()
            return {"accepted": 0, "replay": True}
        metrics.WEBHOOKS.labels("espocrm", "accepted").inc()
        background.add_task(_sync_named, deps, [r.id for r in records])
        return {"accepted": len(records)}

    return app


async def _read_at_most(request: Request, limit: int) -> bytes | None:
    """The request body, or None as soon as it is known to be over `limit`: from Content-Length, or while it
    streams in (a chunked body has none), so an oversized body is never held in memory whole."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


def _triage_answer(run_id: int, result: dict[str, Any], ticket_ref: str | None, replay: bool = False) -> dict[str, Any]:
    out = {
        k: result.get(k) for k in ("intent", "queue", "priority", "needs_approval", "refund_amount", "path", "seconds")
    }
    return {"id": run_id, **out, "ticket": ticket_ref, **({"replay": True} if replay else {})}


def _sync_named(deps: Deps, deal_ids: list[str]) -> None:
    try:
        stats = SyncEngine(deps.crm(), deps.erp(), deps.store).sync_deals(deal_ids)
        metrics.record_sync(stats.summary(), "succeeded")
    except AlreadyRunning:
        # the running sync will reach these deals itself (their modifiedAt is after its cursor)
        logger.info("webhook_sync_deferred", deals=len(deal_ids))
    except Exception:
        logger.exception("webhook_sync_failed", deals=len(deal_ids))
        metrics.SYNC_RUNS.labels("failed").inc()
