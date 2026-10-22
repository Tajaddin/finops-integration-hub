# 0002. The price book is code; a deal whose amount disagrees with it is not invoiced

Date: 2026-10-22. Status: accepted.

## Context

A CRM amount is typed by a person. If the hub copied it onto the invoice, a mistyped amount (an extra zero) would
become a receivable, and an unapproved discount would bypass finance. Apptegy asks for "the reconciliations that
prove both sides agree"; Akoncagua for "approval controls". Both start with knowing what the right amount is.

## Decision

- `src/hub/pricing.py` holds list prices per seat-month per currency (separate price lists, not FX conversions),
  the 15% annual-term discount and the approved deal discounts (0, 5, 10, 15, 20%). The data generator, the sync
  and the reconciliation all use it.
- The sync invoices one line: plan SKU, seat-months, unit price after the term discount, the deal discount as the
  line's discount. A deal whose CRM amount differs from that line's subtotal, or whose discount is not approved,
  is dead-lettered with the reason; it is not invoiced at either price.
- Prices are chosen so every subtotal is a whole number of cents (a test checks every combination), so the hub's
  rounding and Odoo's can never disagree by a cent.
- Invoices are untaxed. Sales tax on SaaS depends on the customer's jurisdiction and is normally a tax engine's job
  (Avalara, Stripe Tax); a fake rule here would be the least credible part of the system.

## Consequences

- Changing a price is a code change with review and tests, which is what finance wants for a price book.
- Monthly deals bill the first month only; recurring billing after that is the ERP's subscription feature and out
  of scope.
