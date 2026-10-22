# Runs inside `odoo shell -d odoo` once the modules are installed (see docker/odoo/init.sh).
# Creates the least-privileged integration user the hub signs in as, gives it a non-expiring API key and writes the
# key to /secrets/odoo_api_key (a bind mount of ./.secrets, git-ignored). Re-running rotates the key.
# `env` is provided by the Odoo shell.
import os
from pathlib import Path

env = env  # type: ignore[name-defined]

admin = env.ref("base.user_admin")
admin.password = os.environ["ODOO_ADMIN_PASSWORD"]

groups = [
    env.ref("sales_team.group_sale_salesman_all_leads"),  # read/write sale orders and partners
    env.ref("account.group_account_invoice"),  # create, post and read customer invoices and payments
]
user = env["res.users"].search([("login", "=", "integration@finops.local")], limit=1)
values = {
    "name": "Integration Hub",
    "login": "integration@finops.local",
    "email": "integration@finops.local",
    "group_ids": [(6, 0, [g.id for g in groups] + [env.ref("base.group_user").id])],
}
if user:
    user.write(values)
else:
    user = env["res.users"].with_context(no_reset_password=True).create(values)

keys = env["res.users.apikeys"].sudo()
keys.search([("user_id", "in", [user.id, admin.id])]).unlink()
key = keys.with_user(user).sudo()._generate(None, "finops-integration-hub", None)
# A second key, for the admin: only the data generator and the fault injector use it, never the hub.
admin_key = keys.with_user(admin).sudo()._generate(None, "finops-test-harness", None)

# Company defaults the generated invoices rely on: USD company plus EUR and GBP active for foreign customers.
company = env.company
company.name = "Ledgerline Inc."
for code in ("EUR", "GBP"):
    env.ref(f"base.{code}").active = True

# The price book's products (src/hub/pricing.py): one service per plan, sold by the seat-month, untaxed.
plans = {
    "LL-STARTER": "Starter plan",
    "LL-TEAM": "Team plan",
    "LL-BUSINESS": "Business plan",
    "LL-ENTERPRISE": "Enterprise plan",
}
for sku, name in plans.items():
    product = env["product.product"].search([("default_code", "=", sku)], limit=1)
    values = {"name": name, "default_code": sku, "type": "service", "sale_ok": True, "taxes_id": [(6, 0, [])]}
    if product:
        product.write(values)
    else:
        env["product.product"].create(values)

for name, value in (("odoo_api_key", key), ("odoo_admin_api_key", admin_key)):
    out = Path("/secrets") / name
    out.write_text(value, encoding="utf-8")
    out.chmod(0o600)
env.cr.commit()
print(f"integration user #{user.id}; API keys written to /secrets")
