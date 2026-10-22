"""Build the live adapters from settings, in one place, so the CLI, the API and the tests wire things the same way."""

from __future__ import annotations

from pathlib import Path

from hub.config import Settings, read_secret
from hub.connectors.base import CrmSource
from hub.connectors.espocrm import Espo, EspoCrm
from hub.connectors.odoo import Odoo, OdooErp
from hub.connectors.salesforce import SalesforceCrm
from hub.store import Store


def odoo(settings: Settings, admin: bool = False) -> Odoo:
    if admin:  # test harness only (data generator, fault injector)
        key_file = (settings.odoo_api_key_file or Path(".secrets/odoo_api_key")).with_name("odoo_admin_api_key")
        secret = read_secret(key_file)
        assert secret is not None
        key = secret.get_secret_value()
    else:
        key = settings.odoo_api_key().get_secret_value()
    return Odoo(settings.odoo_url, settings.odoo_database, key, settings.http_timeout_seconds, settings.http_retries)


def erp(settings: Settings) -> OdooErp:
    return OdooErp(odoo(settings))


def crm(settings: Settings) -> CrmSource:
    if settings.crm == "salesforce":
        if not (settings.salesforce_instance_url and settings.salesforce_client_id):
            raise ValueError("HUB_CRM=salesforce needs HUB_SALESFORCE_INSTANCE_URL and HUB_SALESFORCE_CLIENT_ID")
        secret = read_secret(settings.salesforce_client_secret_file)
        if secret is None:
            raise ValueError("HUB_SALESFORCE_CLIENT_SECRET_FILE is not set")
        return SalesforceCrm(
            settings.salesforce_instance_url,
            settings.salesforce_client_id,
            secret.get_secret_value(),
            api_version=settings.salesforce_api_version,
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
        )
    client = Espo(
        settings.espocrm_url,
        api_key=settings.espocrm_api_key().get_secret_value(),
        timeout=settings.http_timeout_seconds,
        retries=settings.http_retries,
    )
    return EspoCrm(client)


def store(settings: Settings) -> Store:
    return Store(settings.database_url.get_secret_value())
