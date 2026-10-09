#!/bin/bash
# Runs once, the first time the PostgreSQL data volume is created.
# Creates one database and one owner account per tool.
set -euo pipefail

create_db() {
  local name="$1" password="$2"
  if [ -z "$password" ]; then
    echo "Skipping database '$name' (no password set)"
    return
  fi
  echo "Creating database and role '$name'"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE ROLE "$name" LOGIN PASSWORD '$password';
    CREATE DATABASE "$name" OWNER "$name" ENCODING 'UTF8';
EOSQL
}

create_db nautobot  "${NAUTOBOT_DB_PASSWORD:-}"
create_db zabbix    "${ZABBIX_DB_PASSWORD:-}"
create_db semaphore "${SEMAPHORE_DB_PASSWORD:-}"
create_db forgejo   "${FORGEJO_DB_PASSWORD:-}"
create_db keycloak  "${KEYCLOAK_DB_PASSWORD:-}"
create_db grafana   "${GRAFANA_DB_PASSWORD:-}"
