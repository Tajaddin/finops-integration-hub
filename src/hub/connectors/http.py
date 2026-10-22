"""One HTTP policy for every vendor API: timeouts, bounded retries with jittered backoff, Retry-After, typed errors.

The rule that matters for exactly-once: a request that may have reached the server is never retried blindly when
it creates something. Connection failures before the request was sent are always safe to retry; a timeout while
waiting for the response is reported as `AmbiguousWrite`, and the caller re-reads the target by its natural key
before trying again (see sync/engine.py).
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from hub import log

RETRY_STATUS = {429, 502, 503, 504}
logger = log.get(__name__)


class ApiError(Exception):
    def __init__(self, system: str, status: int | None, detail: str) -> None:
        super().__init__(f"{system} {status or ''}: {detail}".strip())
        self.system = system
        self.status = status
        self.detail = detail

    @property
    def transient(self) -> bool:
        return self.status is None or self.status in RETRY_STATUS or self.status >= 500


class AmbiguousWrite(ApiError):
    """The request was sent but no response came back: it may or may not have been applied."""


def backoff(attempt: int, retry_after: str | None = None, base: float = 0.5, cap: float = 20.0) -> float:
    if retry_after:
        try:
            return min(float(retry_after), cap)
        except ValueError:
            pass
    return random.uniform(0, min(cap, base * 2**attempt))  # noqa: S311  (full jitter, not crypto)


class Http:
    def __init__(
        self,
        system: str,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.system = system
        self.retries = retries
        self._sleep = sleep
        self.client = httpx.Client(
            base_url=base_url, headers=headers or {}, timeout=timeout, transport=transport, follow_redirects=False
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, url: str, *, idempotent: bool | None = None, **kwargs: Any) -> httpx.Response:
        """Send with retries. `idempotent` defaults to True for GET/PUT/DELETE/PATCH-by-id semantics of the caller;
        pass False for creates so a read timeout surfaces as AmbiguousWrite instead of a second create."""
        safe = idempotent if idempotent is not None else method.upper() in {"GET", "HEAD", "PUT", "DELETE"}
        attempt = 0
        while True:
            try:
                response = self.client.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                # never reached the server: always safe to retry
                if attempt >= self.retries:
                    raise ApiError(self.system, None, f"unreachable: {exc.__class__.__name__}") from exc
            except httpx.TransportError as exc:
                if not safe:
                    raise AmbiguousWrite(self.system, None, f"{exc.__class__.__name__} after sending") from exc
                if attempt >= self.retries:
                    raise ApiError(self.system, None, f"{exc.__class__.__name__}") from exc
            else:
                if response.status_code < 400:
                    return response
                retryable = response.status_code in RETRY_STATUS and (safe or response.status_code == 429)
                if not retryable or attempt >= self.retries:
                    raise ApiError(self.system, response.status_code, _detail(response))
                delay = backoff(attempt, response.headers.get("Retry-After"))
                logger.warning("http_retry", system=self.system, status=response.status_code, attempt=attempt + 1)
                self._sleep(delay)
                attempt += 1
                continue
            self._sleep(backoff(attempt))
            attempt += 1


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        for key in ("message", "detail", "error", "errorMessages", "name"):
            if key in body:
                return str(body[key])[:300]
    return str(body)[:300]
