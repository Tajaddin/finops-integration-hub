#!/usr/bin/env bash
# One Postgres server for the local stack, one database and one login per system, so each service owns its schema
# and none of them can read another's tables. Runs once, on the first start of an empty data volume.
set -euo pipefail

create() {
  local db=$1 user=$2 password=$3
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE "$user" LOGIN PASSWORD '$password';
CREATE DATABASE "$db" OWNER "$user";
REVOKE ALL ON DATABASE "$db" FROM PUBLIC;
SQL
}

create odoo odoo "$ODOO_DB_PASSWORD"
create espocrm espocrm "$ESPOCRM_DB_PASSWORD"
create hub hub "$HUB_DB_PASSWORD"
create n8n n8n "$N8N_DB_PASSWORD"
create zammad zammad "$ZAMMAD_DB_PASSWORD"
create langfuse langfuse "$LANGFUSE_DB_PASSWORD"

# Odoo initialises the empty "odoo" database it is given, so its role needs neither CREATEDB nor superuser.
# The triage index lives in the hub database (pgvector).
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname hub -c 'CREATE EXTENSION IF NOT EXISTS vector;'
