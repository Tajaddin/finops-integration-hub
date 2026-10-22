"""The two roles the sync needs. Any CRM that can list won deals and take a write-back is a CrmSource; any ERP
that can find or create customers and invoices is an ErpSink. Adapters live next to this file."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from hub.domain import BillingStatus, CrmAccount, CrmDeal, ErpCustomer, ErpInvoice
from hub.pricing import InvoiceLine


class PermanentError(Exception):
    """The record cannot be processed until a person fixes it (bad data, missing field). Dead-lettered, not retried."""


class CrmSource(Protocol):
    name: str

    def won_deals(self, modified_since: datetime | None) -> Iterator[CrmDeal]:
        """Closed-won deals modified after `modified_since`, oldest change first."""
        ...

    def deals(self) -> Iterator[CrmDeal]:
        """Every deal (any stage), for reconciliation."""
        ...

    def account(self, account_id: str) -> CrmAccount: ...

    def write_back(self, deal_id: str, invoice_ref: str | None, status: BillingStatus) -> None:
        """Show the ERP invoice number and billing status on the deal."""
        ...


class ErpSink(Protocol):
    name: str

    def find_customer(self, crm_ref: str, domain: str | None) -> ErpCustomer | None: ...

    def create_customer(self, account: CrmAccount, crm_ref: str) -> ErpCustomer: ...

    def find_invoices(self, origin: str) -> list[ErpInvoice]:
        """Every customer invoice whose source document is `origin` (normally zero or one)."""
        ...

    def create_invoice(self, customer: ErpCustomer, deal: CrmDeal, origin: str, line: InvoiceLine) -> ErpInvoice:
        """A draft invoice. May raise AmbiguousWrite; the caller then looks it up by `origin`."""
        ...

    def post_invoice(self, invoice_id: int) -> ErpInvoice: ...

    def invoice(self, invoice_id: int) -> ErpInvoice: ...

    def invoices(self) -> Iterator[ErpInvoice]: ...

    def customers(self) -> Iterator[ErpCustomer]: ...
