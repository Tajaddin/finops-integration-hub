# Deploying and connecting real accounts

Everything runs locally without any account (`make env up bootstrap`). These are the steps that need the owner's
accounts. None of them has been done yet; each says how to check it worked.

## Salesforce Developer Edition (backlog #19)

1. Sign up at developer.salesforce.com (free; the org stays as long as it is used).
2. Deploy the custom fields in `salesforce/` with the Salesforce CLI:
   `sf org login web --alias finops` then `sf project deploy start --source-dir salesforce/force-app --target-org finops`.
3. Setup > External Client App Manager > New: enable OAuth, the client credentials flow, scopes `api` and
   `refresh_token`; set "Run As" to an integration user with the profile "Salesforce Integration" and a permission
   set that can read Opportunities and Accounts and edit the two write-back fields.
4. Put the consumer secret in a file outside the repository and set:
   ```
   HUB_CRM=salesforce
   HUB_SALESFORCE_INSTANCE_URL=https://<your-domain>.my.salesforce.com
   HUB_SALESFORCE_CLIENT_ID=<consumer key>
   HUB_SALESFORCE_CLIENT_SECRET_FILE=/path/outside/the/repo/salesforce_client_secret
   ```
5. Check: `uv run pytest tests/unit/test_salesforce.py` still passes, then `uv run hub sync` against the org.

## Jira Cloud Free (backlog #20)

1. Create a site at atlassian.com (free for up to 10 users) and a project with key `FIN`.
2. Create an API token at id.atlassian.com > Security, saved to a file outside the repository.
3. Set `HUB_TICKETS=jira`, `HUB_JIRA_URL=https://<site>.atlassian.net`, `HUB_JIRA_EMAIL`, `HUB_JIRA_TOKEN_FILE`.
4. Check: `uv run hub recon` files the open exceptions as Jira tasks; a second run files none.

## GitHub Issues

`HUB_TICKETS=github`, `HUB_GITHUB_REPO=<owner>/<repo>` and `GITHUB_TOKEN` (a fine-grained token with Issues
read/write on that one repository). Use a repository made for it, not this one.

## Oracle Cloud Always Free VM (`infra/terraform`)

1. Create an OCI account (the Always Free tier needs a card for identity checks but is not charged inside the free
   limits; decide that yourself).
2. Create an API key (Profile > API keys), copy `infra/terraform/terraform.tfvars.example` to `terraform.tfvars`
   (git-ignored) and fill it in, with `admin_cidr` set to your own address.
3. `terraform -chdir=infra/terraform init && terraform -chdir=infra/terraform apply`.
4. The VM clones the tag in `git_ref`, generates its own secrets and starts the stack with a CPU model
   (`qwen2.5:3b`: expect several seconds per triaged message without a GPU). Reach the UIs through SSH:
   `ssh -L 3000:127.0.0.1:3000 -L 5678:127.0.0.1:5678 -L 8069:127.0.0.1:8069 ubuntu@<public_ip>`.
5. To accept EspoCRM webhooks and intake mail from the internet, point a DNS name at the VM and set
   `webhook_domain`: Caddy then serves only the two webhook paths over HTTPS.
