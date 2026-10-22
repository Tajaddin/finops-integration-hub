"""Caller authentication (API key) and CRM webhook verification (HMAC), both constant-time."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class ApiKeyCheck:
    """Holds only the SHA-256 of the configured key; compares digests so timing reveals nothing about the key."""

    def __init__(self, key: str) -> None:
        self._digest = digest(key) if key else None

    @property
    def configured(self) -> bool:
        return self._digest is not None

    def ok(self, presented: str | None) -> bool:
        if self._digest is None or not presented:
            return False
        return hmac.compare_digest(digest(presented), self._digest)


@dataclass(frozen=True)
class WebhookSecret:
    webhook_id: str
    secret: str

    @classmethod
    def load(cls, path: Path | None) -> WebhookSecret | None:
        if path is None or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["id"], data["secret"])


def espocrm_signature(webhook_id: str, secret: str, body: bytes) -> str:
    """EspoCRM 9+: base64(webhookId + ':' + hex(HMAC-SHA256(body, secret))) in the `Signature` header."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return base64.b64encode(f"{webhook_id}:{mac}".encode()).decode()


def verify_espocrm(header: str | None, body: bytes, expected: WebhookSecret) -> bool:
    if not header:
        return False
    try:
        decoded = base64.b64decode(header, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return False
    webhook_id, _, mac = decoded.partition(":")
    good = hmac.new(expected.secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(webhook_id, expected.webhook_id) & hmac.compare_digest(mac, good)
