"""`hub` command line: setup, data, sync and reports. Every command prints JSON so scripts and CI can check it."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from hub import factory, log
from hub.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
bootstrap = typer.Typer(no_args_is_help=True)
app.add_typer(bootstrap, name="bootstrap", help="One-time configuration of the local systems")


def _print(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


@app.callback()
def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    log.configure(get_settings().log_level)


@app.command()
def migrate() -> None:
    """Apply the hub's SQL migrations."""
    _print({"applied": factory.store(get_settings()).migrate()})


@bootstrap.command("espocrm")
def bootstrap_espocrm() -> None:
    """Custom fields, currencies, the integration role and API user (key written to the secrets folder)."""
    from hub.bootstrap import bootstrap_espocrm as run

    settings = get_settings()
    password = os.environ.get("ESPOCRM_ADMIN_PASSWORD")
    if not password:
        raise typer.BadParameter("ESPOCRM_ADMIN_PASSWORD is not set (see .env)")
    _print(run(settings.espocrm_url, password, settings.espocrm_api_key_file or Path(".secrets/espocrm_api_key")))


@app.command()
def seed(
    accounts: Annotated[int, typer.Option(min=1, max=5000)] = 300,
    seed: Annotated[int, typer.Option()] = 42,
    out: Annotated[Path, typer.Option(help="ground truth: the book plus the ids each system gave it")] = Path(
        "data/seed.json"
    ),
) -> None:
    """Generate the book of business and load it into EspoCRM (and finance's existing customers into Odoo)."""
    from hub.connectors.espocrm import Espo
    from hub.seed.generator import generate
    from hub.seed.load import load

    settings = get_settings()
    password = os.environ.get("ESPOCRM_ADMIN_PASSWORD")
    if not password:
        raise typer.BadParameter("ESPOCRM_ADMIN_PASSWORD is not set (see .env)")
    book = generate(accounts=accounts, seed=seed)
    ids = load(book, Espo(settings.espocrm_url, admin=("admin", password)), factory.odoo(settings, admin=True))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**book.as_dict(), "ids": ids}, indent=1), encoding="utf-8")
    _print(book.manifest())


@app.command()
def sync(full: Annotated[bool, typer.Option(help="ignore the cursor and look at every won deal")] = False) -> None:
    """Invoice every closed-won deal not invoiced yet; write invoice numbers back to the CRM."""
    from hub.sync.engine import SyncEngine

    settings = get_settings()
    engine = SyncEngine(factory.crm(settings), factory.erp(settings), factory.store(settings))
    _print(engine.run(full=full).summary())


@app.command("dead-letters")
def dead_letters() -> None:
    """Deals the sync could not invoice, with the reason a person must fix."""
    settings = get_settings()
    _print(factory.store(settings).open_dead_letters(settings.crm))


if __name__ == "__main__":
    app()
