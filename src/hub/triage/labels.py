"""The label set (Bitext's 27 intents in 11 categories) and what the hub does with each: which helpdesk queue, which
priority, and whether it is a billing matter that may need the ERP looked up or a refund approved."""

from __future__ import annotations

INTENTS: dict[str, tuple[str, str]] = {
    # intent: (category, one-line description used in prompts)
    "cancel_order": ("ORDER", "wants to cancel an order they placed"),
    "change_order": ("ORDER", "wants to change items or quantities in an existing order"),
    "place_order": ("ORDER", "wants to buy or place a new order"),
    "track_order": ("ORDER", "asks where an order is or what its status is"),
    "change_shipping_address": ("SHIPPING", "wants to change the shipping address of an order"),
    "set_up_shipping_address": ("SHIPPING", "wants to add or set up a shipping address"),
    "delivery_options": ("DELIVERY", "asks which delivery or shipping methods exist"),
    "delivery_period": ("DELIVERY", "asks how long delivery takes or when it will arrive"),
    "check_invoice": ("INVOICE", "wants to check, see or understand an existing invoice or bill"),
    "get_invoice": ("INVOICE", "wants a copy of an invoice or bill sent or downloaded"),
    "check_payment_methods": ("PAYMENT", "asks which payment methods or cards are accepted"),
    "payment_issue": ("PAYMENT", "reports a problem paying, a failed or declined payment, or an unexpected charge"),
    "check_refund_policy": ("REFUND", "asks about the refund policy or whether they are entitled to a refund"),
    "get_refund": ("REFUND", "asks for a refund or money back"),
    "track_refund": ("REFUND", "asks about the status of a refund already requested"),
    "check_cancellation_fee": ("CANCEL", "asks about fees or penalties for cancelling"),
    "create_account": ("ACCOUNT", "wants to open or create an account"),
    "delete_account": ("ACCOUNT", "wants to close or delete their account"),
    "edit_account": ("ACCOUNT", "wants to change account details or profile information"),
    "recover_password": ("ACCOUNT", "forgot or wants to reset their password"),
    "registration_problems": ("ACCOUNT", "cannot sign up or has errors registering"),
    "switch_account": ("ACCOUNT", "wants to switch to another account or plan type"),
    "contact_customer_service": ("CONTACT", "wants to reach customer service (hours, channels)"),
    "contact_human_agent": ("CONTACT", "wants to talk to a human or a live agent"),
    "complaint": ("FEEDBACK", "complains or is unhappy about the service"),
    "review": ("FEEDBACK", "wants to leave a review or give feedback"),
    "newsletter_subscription": ("SUBSCRIPTION", "wants to subscribe or unsubscribe from the newsletter"),
}
CATEGORIES = sorted({c for c, _ in INTENTS.values()})

BILLING = {
    "check_invoice",
    "get_invoice",
    "check_payment_methods",
    "payment_issue",
    "check_refund_policy",
    "get_refund",
    "track_refund",
    "check_cancellation_fee",
}
LOOKS_UP_INVOICES = {"check_invoice", "get_invoice", "payment_issue", "get_refund", "track_refund"}


def category(intent: str) -> str:
    return INTENTS[intent][0]


def queue(intent: str) -> str:
    if intent in {"payment_issue"}:
        return "Collections"
    return "Billing" if intent in BILLING else "Support"


def priority(intent: str) -> str:
    if intent in {"payment_issue", "get_refund", "complaint", "contact_human_agent"}:
        return "high"
    return "low" if intent in {"review", "newsletter_subscription", "delivery_options"} else "normal"


def menu() -> str:
    """The intent list as it appears in prompts."""
    return "\n".join(f"- {name}: {desc}" for name, (_, desc) in INTENTS.items())
