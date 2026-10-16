#!/usr/bin/env bash
# =============================================================================
# Lab: build the whole platform on one machine, create two sample customers with
# simulated devices, and run the end-to-end checks.
#
# Needs: Docker Engine with Compose v2.20 or later, 4 vCPU and 8 GB RAM, Python 3 with
# the "requests" package, openssl, curl. First run: 20 to 30 minutes.
#
#   ./lab/run-lab.sh              build, start, seed and test
#   ./lab/run-lab.sh --no-test    stop after seeding
#
# Behind a TLS-inspecting proxy, export HTTPS_PROXY and LAB_CA_FILE=/path/to/ca.crt first.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
RUN_TESTS=1
[ "${1:-}" = "--no-test" ] && RUN_TESTS=0

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
set_env() {  # set_env KEY VALUE  (values may contain '=' or '/')
  python3 - "$1" "$2" <<'PY'
import sys, pathlib
key, value = sys.argv[1], sys.argv[2]
path = pathlib.Path(".env")
lines = path.read_text().splitlines()
for i, line in enumerate(lines):
    if line.startswith(key + "="):
        lines[i] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n")
PY
}
wait_for() {  # wait_for NAME COMMAND [SECONDS]
  local name="$1" cmd="$2" limit="${3:-600}" waited=0
  printf 'Waiting for %s' "$name"
  until eval "$cmd" >/dev/null 2>&1; do
    sleep 5; waited=$((waited + 5)); printf '.'
    if [ "$waited" -ge "$limit" ]; then echo " timed out"; exit 1; fi
  done
  echo " ready"
}

# The lab includes Grafana (phase 3) so customer dashboard isolation is tested too.
C="docker compose -f compose.yaml -f lab/compose.ports.yaml -f lab/compose.grafana-lab.yaml"
[ -f lab/compose.local.yaml ] && C="$C -f lab/compose.local.yaml"

step "Settings"
[ -f .env ] || ./scripts/gen-env.sh
set_env ZABBIX_PUBLIC_URL http://localhost:8082
set_env GLPI_PUBLIC_URL http://localhost:8083
set_env GRAFANA_URL http://grafana:3000
set_env COMPOSE_PROFILES grafana
set -a; . ./.env; set +a
# A fixed subnet for the platform network: it must not overlap any network you manage, or Docker's
# route to it would hide those devices. The lab checks also need it to give a device its address back.
docker network inspect msp >/dev/null 2>&1 || docker network create --subnet "${MSP_SUBNET:-172.30.0.0/24}" msp >/dev/null
mkdir -p secrets/platform secrets/tenants
# Fixed addresses for the lab devices and the edge collector (.201 to .204 of the platform network).
lab_ip() { python3 -c 'import ipaddress, sys; print(ipaddress.ip_network(sys.argv[1]).network_address + int(sys.argv[2]))' \
  "$(docker network inspect msp -f '{{(index .IPAM.Config 0).Subnet}}')" "$1"; }
LAB_IP_ACME_RTR01=$(lab_ip 201)
LAB_IP_ACME_SRV01=$(lab_ip 202)
LAB_IP_BANKX_RTR01=$(lab_ip 203)
LAB_IP_BANKX_PROXY=$(lab_ip 204)
export LAB_IP_ACME_RTR01 LAB_IP_ACME_SRV01 LAB_IP_BANKX_RTR01 LAB_IP_BANKX_PROXY

step "Building images"
BUILD=(docker build -q)
if [ -n "${HTTPS_PROXY:-}" ]; then
  BUILD+=(--network host --build-arg "HTTPS_PROXY=$HTTPS_PROXY" --build-arg "https_proxy=$HTTPS_PROXY")
fi
CA_DIRS="components/nautobot/certs glue/certs lab/fakenos/certs lab/linux-server/certs"
cleanup_ca() { for d in $CA_DIRS; do rm -f "$d"/lab-ca-*.crt; done; }
trap cleanup_ca EXIT
# One certificate per file: update-ca-certificates in the images skips files holding several.
split_ca() { awk -v dir="$2" '/-----BEGIN CERTIFICATE-----/ {n++; f = sprintf("%s/lab-ca-%d.crt", dir, n)} n && f {print > f} /-----END CERTIFICATE-----/ {close(f); f = ""}' "$1"; }
if [ -n "${LAB_CA_FILE:-}" ]; then for d in $CA_DIRS; do split_ca "$LAB_CA_FILE" "$d"; done; fi
"${BUILD[@]}" --build-arg NAUTOBOT_IMAGE="$NAUTOBOT_IMAGE" -t msp/nautobot:3.2.6-gc3.0.8 components/nautobot
"${BUILD[@]}" -t msp/glue:1.0 glue
"${BUILD[@]}" -t msp/lab-fakenos:1.2.1 lab/fakenos
"${BUILD[@]}" -t msp/lab-linux:1.0 lab/linux-server
cleanup_ca

step "Grafana Zabbix plugin (downloaded once from grafana.com into lab/grafana-plugins)"
PLUGIN_VERSION=6.8.0
if [ ! -f lab/grafana-plugins/alexanderzobnin-zabbix-app/plugin.json ]; then
  mkdir -p lab/grafana-plugins
  curl -sSL -o lab/grafana-plugins/zabbix.zip \
    "https://grafana.com/api/plugins/alexanderzobnin-zabbix-app/versions/$PLUGIN_VERSION/download?os=linux&arch=amd64"
  python3 -c "import zipfile; zipfile.ZipFile('lab/grafana-plugins/zabbix.zip').extractall('lab/grafana-plugins')"
  chmod +x lab/grafana-plugins/alexanderzobnin-zabbix-app/datasource/gpx_zabbix-datasource_* 2>/dev/null || true
  rm -f lab/grafana-plugins/zabbix.zip
fi

step "Starting the platform"
$C up -d postgres mariadb valkey forgejo zabbix-server zabbix-web zabbix-proxy-central glpi semaphore nautobot grafana
wait_for "Nautobot (first start runs about 500 migrations: 5 to 15 minutes)" \
  "[ \"\$(docker inspect -f '{{.State.Health.Status}}' \$($C ps -q nautobot))\" = healthy ]" 1500
wait_for "GLPI" "curl -sf -o /dev/null http://localhost:8083/"
wait_for "Zabbix" "curl -sf -o /dev/null http://localhost:8082/"
wait_for "Semaphore" "curl -sf http://localhost:8084/api/ping"
wait_for "Forgejo" "curl -sf http://localhost:8085/api/healthz"
wait_for "Grafana" "curl -sf http://localhost:8087/api/health"
$C up -d nautobot-worker nautobot-scheduler
wait_for "Nautobot job worker" \
  "[ \"\$(docker inspect -f '{{.State.Health.Status}}' \$($C ps -q nautobot-worker))\" = healthy ]" 600

step "GLPI API access (same as Setup > General > API in the web interface)"
for key in enable_api enable_api_login_credentials enable_api_login_external_token; do
  $C exec -T glpi php bin/console config:set "$key" 1 >/dev/null
done
if [ -z "${GLPI_APP_TOKEN:-}" ]; then
  APP_TOKEN=$(openssl rand -hex 20)
  SUBNET=$(docker network inspect msp -f '{{(index .IPAM.Config 0).Subnet}}')
  read -r START END < <(python3 -c "import ipaddress,sys; n=ipaddress.ip_network('$SUBNET'); print(n.network_address, n.broadcast_address)")
  $C exec -T glpi bash -c "
    S=\$(curl -s -u glpi:glpi http://localhost/apirest.php/initSession | php -r 'echo json_decode(stream_get_contents(STDIN))->session_token;')
    curl -s -X POST -H 'Content-Type: application/json' -H \"Session-Token: \$S\" http://localhost/apirest.php/APIClient \
      -d '{\"input\":{\"name\":\"msp-platform\",\"is_active\":1,\"ipv4_range_start\":\"$START\",\"ipv4_range_end\":\"$END\",\"app_token\":\"$APP_TOKEN\",\"dolog_method\":0}}' >/dev/null
    curl -s -H \"Session-Token: \$S\" http://localhost/apirest.php/killSession >/dev/null"
  set_env GLPI_APP_TOKEN "$APP_TOKEN"
fi

step "Forgejo administrator"
$C exec -T -u git forgejo forgejo admin user create --admin --username "$FORGEJO_ADMIN_USER" \
  --password "$FORGEJO_ADMIN_PASSWORD" --email noc@example.internal --must-change-password=false >/dev/null 2>&1 || true

set -a; . ./.env; set +a
if [ -z "${ZABBIX_API_TOKEN:-}" ]; then
  step "Service accounts and API tokens"
  $C run --rm --no-deps msp-glue mspctl bootstrap tokens --zabbix-password zabbix --glpi-password glpi \
    --semaphore-password "$SEMAPHORE_ADMIN_PASSWORD" --forgejo-user "$FORGEJO_ADMIN_USER" \
    --forgejo-password "$FORGEJO_ADMIN_PASSWORD" | grep -E '^[A-Z_]+=' > secrets/.tokens.tmp
  while IFS= read -r line; do set_env "${line%%=*}" "${line#*=}"; done < secrets/.tokens.tmp
  rm -f secrets/.tokens.tmp
  set -a; . ./.env; set +a
fi
printf '%s' "$FORGEJO_ADMIN_USER" > secrets/platform/forgejo_username
printf '%s' "$FORGEJO_API_TOKEN" > secrets/platform/forgejo_token
chmod 644 secrets/platform/*

step "Platform baseline in every tool"
$C run --rm --no-deps msp-glue mspctl bootstrap platform --webhook-token "$GLPI_ZABBIX_WEBHOOK_TOKEN"
$C up -d msp-glue
wait_for "msp-glue" "curl -sf http://localhost:8086/healthz"

step "Sample customers and simulated devices"
docker compose -p msp-lab-devices -f lab/compose.devices.yaml up -d
python3 lab/seed.py
$C exec -T msp-glue mspctl tenant sync --all
$C exec -T msp-glue mspctl device sync

step "Edge collector for the bank customer (Zabbix proxy with a pre-shared key)"
if [ ! -f lab/.edge-bankx/zabbix_proxy.psk ]; then
  mkdir -p lab/.edge-bankx
  OUT=$($C exec -T msp-glue mspctl proxy add bankx proxy-bankx-01 --address lab-bankx-proxy)
  PSK=$(printf '%s\n' "$OUT" | sed -n 's/^echo \([0-9a-f]*\) > zabbix_proxy.psk.*/\1/p')
  cp edge-collector/compose.yaml lab/.edge-bankx/compose.yaml
  printf 'ZBX_HOSTNAME=proxy-bankx-01\nZBX_SERVER_HOST=zabbix-server\nZBX_TLSPSKIDENTITY=psk-proxy-bankx-01\nZABBIX_IMAGE_TAG=%s\n' \
    "$ZABBIX_IMAGE_TAG" > lab/.edge-bankx/.env
  printf '%s\n' "$PSK" > lab/.edge-bankx/zabbix_proxy.psk
  chmod 644 lab/.edge-bankx/zabbix_proxy.psk
fi
docker compose -p msp-lab-edge-bankx -f lab/.edge-bankx/compose.yaml -f lab/compose.edge-lab.yaml up -d

cat <<EOF

Lab is up:
  Nautobot   http://localhost:8081   (admin / NAUTOBOT_SUPERUSER_PASSWORD in .env)
  Zabbix     http://localhost:8082   (Admin / zabbix: change it now)
  GLPI       http://localhost:8083   (glpi / glpi: change it now)
  Semaphore  http://localhost:8084   (admin / SEMAPHORE_ADMIN_PASSWORD in .env)
  Forgejo    http://localhost:8085   (FORGEJO_ADMIN_USER / FORGEJO_ADMIN_PASSWORD in .env)
  msp-glue   http://localhost:8086/healthz
  Grafana    http://localhost:8087   (admin / GRAFANA_ADMIN_PASSWORD in .env)
EOF

if [ "$RUN_TESTS" = 1 ]; then
  step "End-to-end checks (about 15 minutes)"
  python3 lab/check.py
fi
