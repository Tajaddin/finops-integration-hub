"""Build the live adapters from settings, in one place, so the CLI, the API and the tests wire things the same way."""

from __future__ import annotations

import os
from pathlib import Path

from hub.config import Settings, read_secret
from hub.connectors.base import CrmSource
from hub.connectors.espocrm import Espo, EspoCrm
from hub.connectors.odoo import Odoo, OdooErp
from hub.connectors.salesforce import SalesforceCrm
from hub.store import Store
from hub.tickets import TicketSink
from hub.tickets.github import GitHubTickets
from hub.tickets.jira import JiraTickets
from hub.tickets.zammad import ZammadTickets


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


def tickets(settings: Settings) -> TicketSink | None:
    if settings.tickets == "none":
        return None
    if settings.tickets == "github":
        token = os.environ.get("GITHUB_TOKEN")
        if not (settings.github_repo and token):
            raise ValueError("HUB_TICKETS=github needs HUB_GITHUB_REPO and GITHUB_TOKEN")
        return GitHubTickets(settings.github_repo, token, timeout=settings.http_timeout_seconds)
    if settings.tickets == "jira":
        secret = read_secret(settings.jira_token_file)
        if not (settings.jira_url and settings.jira_email and secret):
            raise ValueError("HUB_TICKETS=jira needs HUB_JIRA_URL, HUB_JIRA_EMAIL and HUB_JIRA_TOKEN_FILE")
        return JiraTickets(settings.jira_url, settings.jira_email, secret.get_secret_value(), settings.jira_project)
    token_secret = read_secret(settings.zammad_token_file)
    if token_secret is None:
        raise ValueError("HUB_ZAMMAD_TOKEN_FILE is not set")
    return ZammadTickets(settings.zammad_url, token_secret.get_secret_value(), timeout=settings.http_timeout_seconds)


def store(settings: Settings) -> Store:
    return Store(settings.database_url.get_secret_value())
