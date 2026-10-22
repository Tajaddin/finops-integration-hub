"""Configure EspoCRM for the hub through its REST API, signed in as the admin once:

- the custom fields the deal needs and the two the hub writes back (idempotent: existing fields are updated);
- USD, EUR and GBP as currencies; the webhook queue job every minute instead of every two;
- a role that can read and edit Accounts and Opportunities (no delete, no other entity) plus webhooks;
- an API user with that role, whose key goes to the secrets folder (never printed).

Odoo is configured by docker/odoo/bootstrap.py inside its container, because API keys can only be minted there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hub import log
from hub.connectors.espocrm import CUSTOM_FIELDS, Espo
from hub.connectors.http import ApiError

WEBHOOK_EVENT = "Opportunity.fieldUpdate.stage"  # a deal reaching Closed Won (skipOwn: not the hub's write-backs)
ROLE_NAME = "Integration Hub"
API_USER = "integration-hub"
logger = log.get(__name__)


def _field_exists(espo: Espo, entity: str, name: str) -> bool:
    fields = espo.http.request("GET", "/Metadata", params={"key": f"entityDefs.{entity}.fields"}).json()
    return f"c{name[0].upper()}{name[1:]}" in fields


def ensure_fields(espo: Espo) -> list[str]:
    changed = []
    for entity, name, definition in CUSTOM_FIELDS:
        full = f"c{name[0].upper()}{name[1:]}"
        if _field_exists(espo, entity, name):
            espo.http.request("PUT", f"/Admin/fieldManager/{entity}/{full}", json={"name": full, **definition})
        else:
            espo.http.request(
                "POST", f"/Admin/fieldManager/{entity}", json={"name": name, **definition}, idempotent=False
            )
        changed.append(f"{entity}.{full}")
    espo.http.request("POST", "/Admin/rebuild", json={}, idempotent=True)
    return changed


def ensure_settings(espo: Espo) -> None:
    espo.http.request(
        "PUT",
        "/Settings",
        json={"currencyList": ["USD", "EUR", "GBP"], "defaultCurrency": "USD", "baseCurrency": "USD"},
    )
    jobs = espo.list_page(
        "ScheduledJob", [{"type": "equals", "attribute": "job", "value": "ProcessWebhookQueue"}], ["id", "scheduling"]
    )
    for job in jobs.get("list", []):
        espo.update("ScheduledJob", job["id"], {"scheduling": "* * * * *"})


def _find(espo: Espo, entity: str, attribute: str, value: str) -> dict[str, Any] | None:
    rows = espo.list_page(
        entity, [{"type": "equals", "attribute": attribute, "value": value}], ["id"], order_by="createdAt"
    )
    found: list[dict[str, Any]] = rows.get("list", [])
    return found[0] if found else None


def ensure_api_user(espo: Espo, key_file: Path) -> str:
    role = {
        "name": ROLE_NAME,
        "data": {
            "Account": {"create": "yes", "read": "all", "edit": "all", "delete": "no", "stream": "no"},
            "Opportunity": {"create": "no", "read": "all", "edit": "all", "delete": "no", "stream": "no"},
            "Webhook": True,
        },
        "fieldData": {},
    }
    existing_role = _find(espo, "Role", "name", ROLE_NAME)
    role_id = existing_role["id"] if existing_role else espo.create("Role", role)["id"]
    if existing_role:
        espo.update("Role", role_id, role)
    user = _find(espo, "User", "userName", API_USER)
    if user is None:
        created = espo.create(
            "User",
            {
                "userName": API_USER,
                "lastName": "Integration Hub",
                "type": "api",
                "authMethod": "ApiKey",
                "rolesIds": [role_id],
            },
        )
        key = created["apiKey"]
    else:
        espo.update("User", user["id"], {"rolesIds": [role_id], "isActive": True})
        key = espo.get("User", user["id"]).get("apiKey")
        if not key:
            raise ApiError(
                "espocrm", None, "existing API user has no readable key; delete it in the admin UI to re-create"
            )
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key, encoding="utf-8")
    key_file.chmod(0o644)  # read by the hub container (another uid); local folder only, see ADR 0005
    return str(user["id"] if user else created["id"])


def ensure_webhook(url: str, api_key: str, target: str, secret_file: Path) -> str:
    """Subscribe the hub to stage changes on deals, as the API user (webhooks belong to the user that made them).
    Re-running replaces the subscription and its secret."""
    espo = Espo(url, api_key=api_key)
    try:
        for hook in espo.list_page("Webhook", [{"type": "equals", "attribute": "url", "value": target}], ["id"]).get(
            "list", []
        ):
            espo.http.request("DELETE", f"/Webhook/{hook['id']}")
        created = espo.create("Webhook", {"event": WEBHOOK_EVENT, "url": target, "skipOwn": True})
    finally:
        espo.close()
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(json.dumps({"id": created["id"], "secret": created["secretKey"]}), encoding="utf-8")
    secret_file.chmod(0o644)
    return str(created["id"])


def bootstrap_espocrm(
    url: str, admin_password: str, key_file: Path, secret_file: Path, webhook_target: str
) -> dict[str, Any]:
    espo = Espo(url, admin=("admin", admin_password))
    try:
        fields = ensure_fields(espo)
        ensure_settings(espo)
        user_id = ensure_api_user(espo, key_file)
    finally:
        espo.close()
    webhook = ensure_webhook(url, key_file.read_text(encoding="utf-8").strip(), webhook_target, secret_file)
    logger.info("espocrm_bootstrapped", fields=len(fields), api_user=user_id, webhook=webhook)
    return {"fields": fields, "api_user": user_id, "webhook": webhook}
