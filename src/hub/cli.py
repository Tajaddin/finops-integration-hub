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
    target = os.environ.get("HUB_WEBHOOK_TARGET", "http://hub:8000/webhooks/espocrm")
    _print(
        run(
            settings.espocrm_url,
            password,
            settings.espocrm_api_key_file or Path(".secrets/espocrm_api_key"),
            settings.espocrm_webhook_secret_file or Path(".secrets/espocrm_webhook_secret"),
            target,
        )
    )


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
    out.write_text(json.dumps({**book.as_dict(), "ids": ids}, indent=1), encoding="utf-8", newline="\n")
    _print(book.manifest())


@app.command()
def sync(full: Annotated[bool, typer.Option(help="ignore the cursor and look at every won deal")] = False) -> None:
    """Invoice every closed-won deal not invoiced yet; write invoice numbers back to the CRM."""
    from hub.sync.engine import SyncEngine

    settings = get_settings()
    engine = SyncEngine(factory.crm(settings), factory.erp(settings), factory.store(settings))
    _print(engine.run(full=full).summary())


@app.command("billing-status")
def billing_status() -> None:
    """Write the ERP's billing status (invoiced, partial, paid, overdue) back to the CRM where it differs."""
    from hub.sync.engine import SyncEngine

    settings = get_settings()
    _print(SyncEngine(factory.crm(settings), factory.erp(settings), factory.store(settings)).refresh_billing_status())


@app.command()
def recon(tickets: Annotated[bool, typer.Option(help="file and close tickets")] = True) -> None:
    """Compare both systems in full; store the exceptions; route them to the ticket system."""
    from hub.recon.run import run_reconciliation

    settings = get_settings()
    sink = factory.tickets(settings) if tickets else None
    stats, _ = run_reconciliation(factory.crm(settings), factory.erp(settings), factory.store(settings), sink)
    _print(stats)


@app.command("recon-eval")
def recon_eval(
    per_class: Annotated[int, typer.Option(min=1, max=50)] = 8,
    seed: Annotated[int, typer.Option()] = 1,
    book: Annotated[Path, typer.Option(help="the seed file, for the deals that carry data problems")] = Path(
        "data/seed.json"
    ),
    out: Annotated[Path, typer.Option()] = Path("reports/recon/eval.json"),
) -> None:
    """The measured scenario: payments, faults injected at known counts, precision and recall, tickets (test harness)."""
    from hub.connectors.espocrm import Espo
    from hub.connectors.odoo import OdooErp
    from hub.recon.scenario import evaluate

    settings = get_settings()
    password = os.environ.get("ESPOCRM_ADMIN_PASSWORD")
    if not password:
        raise typer.BadParameter("ESPOCRM_ADMIN_PASSWORD is not set (see .env)")
    natural = None
    if book.is_file():
        data = json.loads(book.read_text(encoding="utf-8"))
        problems = [d["key"] for d in data["deals"] if d["stage"] == "Closed Won" and d.get("data_problem")]
        natural = {("missing_invoice", data["ids"]["deals"][k]) for k in problems}
    report = evaluate(
        factory.crm(settings),
        factory.erp(settings),
        OdooErp(factory.odoo(settings, admin=True)),
        Espo(settings.espocrm_url, admin=("admin", password)),
        factory.store(settings),
        factory.tickets(settings),
        per_class=per_class,
        seed=seed,
        natural=natural,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    _print({k: report[k] for k in ("injected", "score", "tickets_first_run", "tickets_rerun", "after_repair")})


@app.command("triage-index")
def triage_index(
    embed: Annotated[
        bool, typer.Option(help="also embed into pgvector (needs Ollama); off for the CI stand-in")
    ] = True,
) -> None:
    """Train the TF-IDF baseline and embed the training messages and help-centre articles into pgvector."""
    from hub.triage import runtime

    settings = get_settings()
    runtime.train_baseline()
    _print(runtime.build_indexes(settings) if embed else {"baseline": str(runtime.BASELINE)})


@app.command("triage-eval")
def triage_eval(
    model: Annotated[str | None, typer.Option(help="Ollama model; default HUB_TRIAGE_MODEL")] = None,
    per_intent: Annotated[int, typer.Option(min=1, max=200)] = 40,
    out: Annotated[Path, typer.Option()] = Path("reports/triage/eval.json"),
) -> None:
    """Compare TF-IDF, zero-shot, retrieval few-shot and the cascade on the Bitext sample and the challenge set,
    then run the whole agent on the challenge set."""
    import time

    from hub.triage import eval as tev
    from hub.triage import runtime
    from hub.triage.data import eval_sample, messages, split
    from hub.triage.llm import LlmClassifier

    settings = get_settings()
    name = model or settings.triage_model
    train, test = split(messages())
    bitext = tev.as_cases(eval_sample(test, per_intent=per_intent))
    challenge = tev.challenge_set()
    baseline = runtime.load_baseline()
    the_llm = runtime.llm(settings, name)
    examples, _kb = runtime.retrievers(settings)
    zero, few = LlmClassifier(the_llm), LlmClassifier(the_llm, examples)

    def progress(label: str, i: int) -> None:
        if i % 100 == 0:
            sys.stderr.write(f"{label}: {i}\n")

    started = time.time()
    report: dict[str, Any] = {
        "model": name,
        "embedding_model": settings.embed_model,
        "hardware": "one workstation: RTX 4060 8 GB (Ollama), i5-13600KF",
        "train_messages": len(train),
        "bitext_sample": tev.compare("bitext", bitext, baseline, zero, few, progress=progress),
        "challenge_set": tev.compare("challenge", challenge, baseline, zero, few, progress=progress),
    }
    workflow = runtime.workflow(settings, factory.erp(settings), model=name)
    report["agent_on_challenge_set"] = tev.agent_on_challenge(workflow, challenge, settings.refund_approval_threshold)
    report["minutes"] = round((time.time() - started) / 60, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    summary = {
        k: {a: v.get("intent_accuracy") for a, v in report[k].items() if isinstance(v, dict)}
        for k in ("bitext_sample", "challenge_set")
    }
    agent = {k: v for k, v in report["agent_on_challenge_set"].items() if k != "rows"}
    _print({"model": name, "accuracy": summary, "agent": agent})


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, workers: int = 1) -> None:
    """Run the API (the container runs uvicorn directly)."""
    import uvicorn

    uvicorn.run("hub.api.main:app", host=host, port=port, workers=workers)


@app.command("dead-letters")
def dead_letters() -> None:
    """Deals the sync could not invoice, with the reason a person must fix."""
    settings = get_settings()
    _print(factory.store(settings).open_dead_letters(settings.crm))


if __name__ == "__main__":
    app()
