#!/usr/bin/env bash
# =============================================================================
# Installer for the IOH open-source managed services platform on one Ubuntu server.
#
#   ./scripts/install.sh preflight      check this server; changes nothing
#   ./scripts/install.sh settings --domain msp.ioh.internal --subnet 172.30.0.0/24 \
#        --admin-networks "10.10.0.0/16 10.20.0.0/16" --tz Asia/Jakarta
#   ./scripts/install.sh                run every core stage that is not done yet, in order
#   ./scripts/install.sh status         show which stages are done
#   ./scripts/install.sh <stage>        run one stage again, for example after fixing its cause
#
# Core stages, in this order:
#   preflight settings network images start glpi-api forgejo-admin tokens platform glue harden check
# Later stages, when you are ready for them:
#   proxy     HTTPS for every tool on https://<tool>.<domain> through Caddy (needs the DNS names)
#   grafana   customer dashboards (phase 3 of the build plan)
#   certs <fullchain.pem> <privkey.pem>   use IOH certificates instead of Caddy's own (certs internal: back)
#
# Every stage can run again safely. A failed stage stops with the reason, the fix and the command
# that continues after the fix. Output goes to install.log.
#
# Prepare the server first with: sudo ./scripts/prepare-server.sh --admin-networks "<NOC and VPN ranges>"
# Behind a web proxy, prepare-server.sh --proxy (and --proxy-ca) sets everything up; by hand, export
# HTTPS_PROXY before running and, for a proxy that inspects TLS, CA_FILE=/path/to/proxy-ca.crt.
# Run this installer without sudo, from the account that owns the kit folder (root works too, if root
# owns the folder). After prepare-server.sh, log out and in again so the docker group applies.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
KIT_DIR=$(pwd)
STATE_FILE=.install-state
LOG_FILE=install.log
CORE_STAGES=(preflight settings network images start glpi-api forgejo-admin tokens platform glue harden check)
ALL_STAGES=("${CORE_STAGES[@]}" proxy grafana)
NAUTOBOT_TAG=msp/nautobot:3.2.6-gc3.0.8
GLUE_TAG=msp/glue:1.0
GRAFANA_PLUGIN_VERSION=6.8.0

# ----------------------------------------------------------------------------- output
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; Y=; R=; N=; fi
WARNINGS=0
PROBLEMS=0
CURRENT_STAGE=""
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %sOK%s     %s\n' "$G" "$N" "$*"; }
info() { printf '         %s\n' "$*"; }
warn() { printf '  %sWARN%s   %s\n' "$Y" "$N" "$1"; [ -n "${2:-}" ] && printf '         Fix: %s\n' "$2"; WARNINGS=$((WARNINGS + 1)); }
bad()  { printf '  %sFAIL%s   %s\n' "$R" "$N" "$1"; [ -n "${2:-}" ] && printf '         Fix: %s\n' "$2"; PROBLEMS=$((PROBLEMS + 1)); }
FAIL_SHOWN=""
RUN_CMD="./scripts/install.sh"
then_hint() {  # how to continue after a fix: core stages resume with a plain run, the others need their own command
  case " ${CORE_STAGES[*]} " in
    *" ${CURRENT_STAGE:-none} "*) printf '  Then run ./scripts/install.sh again: it continues with stage "%s".\n' "$CURRENT_STAGE" ;;
    *) case "${1:-}" in ./scripts/install.sh*) ;; *) printf '  Then run the same command again: %s\n' "$RUN_CMD" ;; esac ;;
  esac
}
fail() {  # fail "what went wrong" "how to fix it": stops the run
  printf '\n  %sSTOPPED%s %s\n' "$R" "$N" "$1"
  [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2"
  then_hint "${2:-}"
  FAIL_SHOWN=1
  exit 1
}
on_exit() {  # any other error that stopped the script
  local rc=$?
  if [ "$rc" -ne 0 ] && [ -z "$FAIL_SHOWN" ]; then
    printf '\n  %sSTOPPED%s stage "%s" ended with an error (exit code %s).\n' "$R" "$N" "${CURRENT_STAGE:-?}" "$rc"
    printf '  The lines just above show the cause; the whole run is in %s/%s.\n' "$KIT_DIR" "$LOG_FILE"
    printf '  Fix the cause.\n'
    then_hint
  fi
}
trap on_exit EXIT

# ---------------------------------------------------------------------------- account
in_group() {  # in_group GROUP [USER]: with USER, that account's groups in the user database; without, this login's groups
  local groups
  if [ -n "${2:-}" ]; then groups=$(id -nG "$2" 2>/dev/null || true); else groups=$(id -nG 2>/dev/null || true); fi
  case " $groups " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}
check_account() {  # one account runs the installer: the owner of the kit folder, without sudo
  local me owner stray
  me=$(id -un)
  owner=$(stat -c %U "$KIT_DIR")
  if [ "$me" != "$owner" ]; then
    if [ "$me" = root ] && [ "${SUDO_USER:-}" = "$owner" ]; then
      fail "the installer runs without sudo, as $owner (files made with sudo would belong to root and lock $owner out)" \
        "leave out sudo (in a root shell from sudo -i or sudo su, type exit first). If Docker then says permission denied, log out and log in again (with tmux, run tmux kill-server first)"
    fi
    fail "the kit folder $KIT_DIR belongs to '$owner', but this login is '$me'" \
      "log in as $owner and run the command there, or make '$me' the owner: sudo chown -R $me: $KIT_DIR"
  fi
  # lab/ is left out: the lab's Grafana container writes into lab/grafana-plugins as its own user.
  stray=$(find "$KIT_DIR" -xdev -path "$KIT_DIR/lab" -prune -o ! -user "$me" -print -quit 2>/dev/null || true)
  if [ -n "$stray" ]; then
    fail "${stray#"$KIT_DIR"/} belongs to another account (an earlier run with sudo, or files copied in as root)" \
      "make '$me' the owner of everything in the kit folder: sudo chown -R $me: $KIT_DIR"
  fi
}

# ------------------------------------------------------------------------------ state
is_done()   { [ -f "$STATE_FILE" ] && cut -f1 "$STATE_FILE" | grep -qx -- "$1"; }
mark_done() { is_done "$1" || printf '%s\t%s\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$STATE_FILE"; }
unmark()    { if [ -f "$STATE_FILE" ]; then grep -v -- "^$1"$'\t' "$STATE_FILE" > "$STATE_FILE.tmp" || true; mv "$STATE_FILE.tmp" "$STATE_FILE"; fi; }

# -------------------------------------------------------------------------- .env helpers
load_env() { if [ -f .env ]; then set -a; . ./.env; set +a; fi; }
set_env() {  # set_env KEY VALUE: replace or add KEY=VALUE in .env (values with spaces are quoted)
  python3 - "$1" "$2" <<'PY'
import pathlib, sys
key, value = sys.argv[1], sys.argv[2]
if any(c in value for c in " \t#"):
    value = '"' + value + '"'
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
add_profile() {  # add_profile NAME: switch an optional component on (COMPOSE_PROFILES in .env, read by docker compose)
  load_env
  local current=${COMPOSE_PROFILES:-}
  case ",$current," in *",$1,"*) return 0 ;; esac
  set_env COMPOSE_PROFILES "${current:+$current,}$1"
  load_env
}

# ---------------------------------------------------------------------- docker helpers
dc() { docker compose "$@"; }
dc_up() {  # dc_up SERVICE...: start containers quietly; on failure show Docker's own message
  local out
  if ! out=$(docker compose up -d "$@" 2>&1); then
    printf '%s\n' "$out" | grep -v -E ' (Waiting|Healthy|Running|Starting|Started|Creating|Created|Recreate|Recreated)$' | tail -n 15
    return 1
  fi
}
container_of() { dc ps -q "$1" 2>/dev/null | head -n 1 || true; }
health_is() {  # health_is SERVICE STATUS: STATUS is healthy (with a health check) or running
  local id; id=$(container_of "$1")
  [ -n "$id" ] && [ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")" = "$2" ]
}
all_healthy() { local s; for s in "$@"; do health_is "$s" healthy || return 1; done; }
probe() {  # probe URL: true when the URL answers on the msp network with a status below 500
  docker run --rm --network msp --entrypoint python "$GLUE_TAG" -c '
import sys, urllib.request, urllib.error
try:
    urllib.request.urlopen(sys.argv[1], timeout=5)
except urllib.error.HTTPError as e:
    sys.exit(0 if e.code < 500 else 1)
except Exception:
    sys.exit(1)' "$1" >/dev/null 2>&1
}
wait_for() {  # wait_for "what" SECONDS "fix hint" COMMAND...
  local what=$1 limit=$2 hint=$3 waited=0
  shift 3
  printf '  ....   waiting for %s ' "$what"
  until "$@"; do
    sleep 5
    waited=$((waited + 5))
    printf '.'
    if [ "$waited" -ge "$limit" ]; then
      printf '\n'
      fail "$what was not ready after $((limit / 60)) minutes" "$hint"
    fi
  done
  printf ' ready after %ss\n' "$waited"
}
split_ca() {  # split_ca FILE DIR: one certificate per file, as update-ca-certificates in the images expects
  awk -v dir="$2" '/-----BEGIN CERTIFICATE-----/ {n++; f = sprintf("%s/custom-ca-%d.crt", dir, n)} n && f {print > f} /-----END CERTIFICATE-----/ {close(f); f = ""}' "$1"
}
ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]; }  # ver_ge A B: A >= B
reaches() {  # reaches HOST: true when https://HOST/ gives any HTTP answer (through HTTPS_PROXY if set)
  local code
  code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "https://$1/" || true)
  [ "${code:-000}" != 000 ]
}
glpi_console() { dc exec -T glpi php bin/console "$@" >/dev/null; }

# ======================================================================= stages
stage_preflight() {
  step "Preflight: checking this server (nothing is changed)"
  local os_id os_name cpus mem_gb root free_gb v host code
  os_id=$( (. /etc/os-release 2>/dev/null; echo "${ID:-}-${VERSION_ID:-}") )
  os_name=$( (. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}") )
  case "$os_id" in
    ubuntu-24.04 | ubuntu-22.04) ok "Operating system: $os_name" ;;
    *) warn "Operating system: $os_name (the kit was tested on Ubuntu 24.04)" ;;
  esac
  cpus=$(nproc)
  if [ "$cpus" -ge 4 ]; then ok "$cpus CPUs"
  elif [ "$cpus" -ge 2 ]; then warn "$cpus CPUs: works, but the first start is slow" "give the virtual machine 4 CPUs (8 for production)"
  else bad "$cpus CPU" "give the virtual machine at least 2 CPUs (4 or more recommended)"; fi
  mem_gb=$(awk '/MemTotal/ {printf "%d", $2 / 1048576 + 0.5}' /proc/meminfo)
  if [ "$mem_gb" -ge 8 ]; then ok "${mem_gb} GB memory"
  elif [ "$mem_gb" -ge 6 ]; then warn "${mem_gb} GB memory: tight" "give the virtual machine 8 GB or more (32 GB for production)"
  else bad "${mem_gb} GB memory" "give the virtual machine at least 8 GB"; fi

  for tool in python3 curl openssl ip ss; do
    command -v "$tool" >/dev/null || bad "command '$tool' is missing" "sudo apt-get install -y python3 curl openssl iproute2"
  done
  if ! command -v docker >/dev/null; then
    bad "Docker is not installed" "sudo ./scripts/prepare-server.sh --admin-networks \"<NOC and VPN ranges>\" (installs and sets up Docker)"
  elif ! docker info >/dev/null 2>&1; then
    if [ "$(id -u)" != 0 ] && ! in_group docker "$(id -un)"; then
      bad "Docker is installed, but user '$(id -un)' cannot use it" "sudo usermod -aG docker $(id -un), then log out and log in again"
    elif [ "$(id -u)" != 0 ] && ! in_group docker; then
      bad "user '$(id -un)' joined the docker group after this login started, so the group does not apply yet" \
        "log out and log in again (close the terminal or SSH session and open a new one; with tmux, run tmux kill-server first). Do not use sudo for the installer"
    else
      bad "Docker is installed, but does not answer: $(docker info 2>&1 >/dev/null | grep -v '^$' | tail -n 1 | cut -c1-200)" \
        "sudo systemctl restart docker; if that fails, sudo journalctl -u docker --no-pager | tail -n 20 shows why"
    fi
  else
    ok "Docker Engine $(docker version -f '{{.Server.Version}}' 2>/dev/null)"
    v=$(docker compose version --short 2>/dev/null | sed 's/^v//' || true)
    if [ -z "$v" ]; then bad "Docker Compose plugin missing" "sudo apt-get install -y docker-compose-plugin"
    elif ver_ge "$v" 2.20; then ok "Docker Compose $v"
    else bad "Docker Compose $v is too old (2.20 or later needed)" "sudo apt-get install --only-upgrade docker-compose-plugin"; fi
    root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
    [ -d "$root" ] || root=/
    free_gb=$(df -P -BG "$root" 2>/dev/null | awk 'NR == 2 {gsub("G", "", $4); print $4}')
    if [ "${free_gb:-0}" -ge 40 ]; then ok "${free_gb} GB free for Docker ($root)"
    elif [ "${free_gb:-0}" -ge 15 ]; then warn "${free_gb} GB free for Docker ($root): enough to install, not to run customers" "give $root its own disk of 100 GB or more before customers go live"
    else bad "only ${free_gb:-0} GB free for Docker ($root)" "add disk space: at least 40 GB, 100 GB or more for production"; fi
    if grep -qs default-address-pools /etc/docker/daemon.json; then ok "Docker address pools set in /etc/docker/daemon.json"
    else warn "Docker uses its default address pools (172.17.0.0/16 to 172.31.0.0/16, 192.168.0.0/16)" \
      "sudo ./scripts/prepare-server.sh --admin-networks \"<NOC and VPN ranges>\" --pool <range no managed network uses> sets them"; fi
    if [ -n "${HTTPS_PROXY:-${https_proxy:-}}" ] && [ -z "$(docker info -f '{{.HTTPSProxy}}' 2>/dev/null || true)" ]; then
      warn "this shell uses a web proxy, but the Docker daemon has none, so image downloads may fail" \
        "sudo ./scripts/prepare-server.sh ... --proxy <proxy URL> sets it for the Docker service"
    fi
  fi

  if command -v ss >/dev/null; then
    if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq '[:.]10051$' && [ -z "$(container_of zabbix-server 2>/dev/null)" ]; then
      bad "port 10051 is already in use (the Zabbix server needs it)" "stop the program using it: sudo ss -ltnp | grep 10051"
    else ok "port 10051 free for the Zabbix server"; fi
    for p in 80 443; do
      if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$p\$" && [ -z "$(container_of proxy 2>/dev/null)" ]; then
        warn "port $p is in use; the reverse proxy needs it later" "stop the web server using it: sudo ss -ltnp | grep ':$p '"
      fi
    done
  fi
  if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo unknown)" = yes ]; then ok "clock synchronised"
  else warn "clock not synchronised" "sudo timedatectl set-ntp true (or point chrony at IOH's time servers)"; fi

  # Needed now: Docker Hub (registry, log-in, image layers), Codeberg (Forgejo image), PyPI (the two image builds).
  for host in registry-1.docker.io auth.docker.io production.cloudfront.docker.com codeberg.org pypi.org files.pythonhosted.org; do
    if reaches "$host"; then ok "reaches $host"
    else bad "cannot reach https://$host" "allow HTTPS to it in the firewall, or export HTTPS_PROXY=http://<proxy>:<port>"; fi
  done
  # Needed later: grafana.com for customer dashboards, quay.io for single sign-on (both phase 3).
  for host in grafana.com quay.io; do
    if reaches "$host"; then ok "reaches $host"
    else warn "cannot reach https://$host (only needed in phase 3)" "allow HTTPS to it before you switch on dashboards or single sign-on"; fi
  done

  # The platform range from .env once it exists; before that, MSP_SUBNET=<range> on the command line, or the default.
  local subnet=${MSP_SUBNET:-172.30.0.0/24}
  if [ -f .env ]; then subnet=$( (load_env; echo "${MSP_SUBNET:-172.30.0.0/24}") ); fi
  check_subnet "$subnet" || true
  check_pools

  if [ "$PROBLEMS" -gt 0 ]; then
    fail "$PROBLEMS problem(s) found, marked FAIL above" "fix each one, then run ./scripts/install.sh preflight again"
  fi
  ok "preflight passed with $WARNINGS warning(s)"
}

check_subnet() {  # check_subnet CIDR: report routes on this server that overlap the platform network
  local overlaps
  # Routes on Docker's own bridges (docker0, br-...) are skipped: one of them is the msp network itself.
  overlaps=$(ip -4 route show 2>/dev/null | python3 -c '
import ipaddress, sys
want = ipaddress.ip_network(sys.argv[1], strict=False)
for line in sys.stdin:
    parts = line.split()
    if not parts or parts[0] == "default":
        continue
    dev = parts[parts.index("dev") + 1] if "dev" in parts[:-1] else ""
    if dev == "docker0" or dev.startswith("br-"):
        continue
    try:
        net = ipaddress.ip_network(parts[0], strict=False)
    except ValueError:
        continue
    if net.overlaps(want):
        print(line.strip())' "$1")
  if [ -n "$overlaps" ]; then
    bad "the platform network $1 overlaps routes on this server: $(echo "$overlaps" | head -n 2 | tr '\n' ';')" \
      "choose another range with: ./scripts/install.sh settings --subnet <range>"
    return 1
  fi
  # It must also stay outside Docker's own address pool (set by prepare-server.sh or by hand).
  local pools
  pools=$(python3 -c 'import json; print(" ".join(p["base"] for p in json.load(open("/etc/docker/daemon.json")).get("default-address-pools", [])))' 2>/dev/null || true)
  if [ -n "$pools" ] && python3 -c '
import ipaddress, sys
want = ipaddress.ip_network(sys.argv[1], strict=False)
sys.exit(0 if any(want.overlaps(ipaddress.ip_network(p, strict=False)) for p in sys.argv[2:]) else 1)' "$1" $pools; then
    bad "the platform network $1 lies inside Docker's address pool ($pools)" \
      "choose a platform range outside it with: ./scripts/install.sh settings --subnet <range>"
    return 1
  fi
  ok "platform network $1 overlaps no route on this server"
}

check_pools() {  # warn when Docker's address pools overlap routes on this server that Docker did not create
  local pools overlaps
  pools=$(python3 -c 'import json; print(" ".join(p["base"] for p in json.load(open("/etc/docker/daemon.json")).get("default-address-pools", [])))' 2>/dev/null || true)
  [ -n "$pools" ] || return 0
  overlaps=$(ip -4 route show 2>/dev/null | python3 -c '
import ipaddress, sys
pools = [ipaddress.ip_network(p, strict=False) for p in sys.argv[1:]]
for line in sys.stdin:
    parts = line.split()
    if not parts or parts[0] == "default" or "docker0" in parts or any(p.startswith("br-") for p in parts):
        continue
    try:
        net = ipaddress.ip_network(parts[0], strict=False)
    except ValueError:
        continue
    if any(net.overlaps(p) for p in pools):
        print(line.strip())' $pools)
  if [ -n "$overlaps" ]; then
    warn "Docker's address pool ($pools) overlaps routes on this server: $(echo "$overlaps" | head -n 2 | tr '\n' ';')" \
      "choose another pool in /etc/docker/daemon.json and restart Docker before the installer creates its networks"
  else
    ok "Docker's address pool ($pools) overlaps no route on this server"
  fi
}

stage_settings() {
  step "Settings (.env)"
  local domain="" nets="" subnet="" tz="" zurl="" gurl="" email=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --domain) domain=${2:-}; shift 2 ;;
      --admin-networks) nets=${2:-}; shift 2 ;;
      --subnet) subnet=${2:-}; shift 2 ;;
      --tz) tz=${2:-}; shift 2 ;;
      --zabbix-url) zurl=${2:-}; shift 2 ;;
      --glpi-url) gurl=${2:-}; shift 2 ;;
      --email) email=${2:-}; shift 2 ;;
      *) fail "unknown option '$1' for settings" "use --domain --admin-networks --subnet --tz --email (and optionally --zabbix-url --glpi-url)" ;;
    esac
  done
  if [ ! -f .env ]; then
    ./scripts/gen-env.sh
  fi
  chmod 600 .env
  python3 - "$domain" "$nets" "$subnet" "$tz" "$email" <<'PY' || fail "a setting is not valid (see above)" "correct it and run the settings command again"
import ipaddress, os, re, sys
domain, nets, subnet, tz, email = sys.argv[1:6]
errors = []
if domain and not re.fullmatch(r"(?=.{1,200}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", domain):
    errors.append(f"--domain '{domain}' is not a DNS domain in lowercase, for example msp.ioh.internal")
for n in nets.split():
    try:
        ipaddress.ip_network(n, strict=False)
    except ValueError:
        errors.append(f"--admin-networks: '{n}' is not an address range such as 10.10.0.0/16")
if subnet:
    try:
        net = ipaddress.ip_network(subnet, strict=True)
        if net.prefixlen > 26:
            errors.append(f"--subnet {subnet} is too small: use a /24 (or at least a /26)")
    except ValueError:
        errors.append(f"--subnet '{subnet}' is not a network address such as 172.30.0.0/24")
if tz and not os.path.exists(f"/usr/share/zoneinfo/{tz}"):
    errors.append(f"--tz '{tz}' is not a known time zone, for example Asia/Jakarta")
if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
    errors.append(f"--email '{email}' is not an e-mail address")
for e in errors:
    print("  FAIL   " + e)
sys.exit(1 if errors else 0)
PY
  if [ -n "$subnet" ] && docker network inspect msp >/dev/null 2>&1; then
    local have; have=$(docker network inspect msp -f '{{(index .IPAM.Config 0).Subnet}}')
    [ "$have" = "$subnet" ] || fail "the msp network already exists with $have" \
      "keep it (--subnet $have), or stop the platform and remove the network first (docker compose down; docker network rm msp)"
  fi
  if [ -n "$subnet" ]; then check_subnet "$subnet" || fail "the platform network range overlaps a route" "pick a range nothing else uses"; fi
  if [ -n "$domain" ]; then
    set_env MSP_DOMAIN "$domain"
    set_env ZABBIX_PUBLIC_URL "${zurl:-https://zabbix.$domain}"
    set_env GLPI_PUBLIC_URL "${gurl:-https://glpi.$domain}"
  else
    [ -n "$zurl" ] && set_env ZABBIX_PUBLIC_URL "$zurl"
    [ -n "$gurl" ] && set_env GLPI_PUBLIC_URL "$gurl"
  fi
  [ -n "$nets" ] && set_env ADMIN_NETWORKS "$nets"
  [ -n "$subnet" ] && set_env MSP_SUBNET "$subnet"
  [ -n "$tz" ] && set_env TZ "$tz"
  [ -n "$email" ] && set_env NAUTOBOT_SUPERUSER_EMAIL "$email"
  load_env
  ok "domain           $MSP_DOMAIN"
  ok "admin networks   $ADMIN_NETWORKS"
  ok "platform network ${MSP_SUBNET:-172.30.0.0/24}"
  ok "time zone        $TZ"
  ok "Zabbix and GLPI  $ZABBIX_PUBLIC_URL  $GLPI_PUBLIC_URL"
  if [ "$MSP_DOMAIN" = msp.example.internal ]; then
    fail "the domain is still the example value msp.example.internal" "./scripts/install.sh settings --domain <your domain, for example msp.ioh.internal>"
  fi
  info "All passwords and keys were generated into .env (readable by you only). Keep a copy in your password manager."
}

stage_network() {
  step "Platform network"
  load_env
  local want=${MSP_SUBNET:-172.30.0.0/24} have
  if docker network inspect msp >/dev/null 2>&1; then
    have=$(docker network inspect msp -f '{{(index .IPAM.Config 0).Subnet}}')
    [ "$have" = "$want" ] || fail "network msp exists with $have, but MSP_SUBNET in .env is $want" \
      "run ./scripts/install.sh settings --subnet $have, or remove the network while nothing uses it: docker network rm msp"
    ok "network msp already exists ($have)"
  else
    docker network create --subnet "$want" msp >/dev/null ||
      fail "could not create network msp with $want" "choose a free range: ./scripts/install.sh settings --subnet <range>"
    ok "network msp created ($want)"
  fi
  mkdir -p secrets/platform secrets/tenants
  chmod 755 secrets secrets/platform secrets/tenants
  ok "secrets folders ready"
}

stage_images() {
  step "Container images (5 to 30 minutes the first time, depending on the Internet link)"
  load_env
  local img out reason rate_limited=0
  for img in $(dc config --images | sort -u | grep -v '^msp/'); do
    if out=$(docker pull -q "$img" 2>&1); then ok "downloaded $img"
    elif docker image inspect "$img" >/dev/null 2>&1; then warn "could not refresh $img; the copy already on this server is used"
    else
      reason=$(printf '%s\n' "$out" | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c1-400)
      case "$reason" in
        *toomanyrequests* | *"Too Many Requests"* | *" 429"*) rate_limited=1; bad "could not download $img: Docker Hub's download limit was reached" \
          "run docker login with a free Docker account (or wait a few hours), then run ./scripts/install.sh again" ;;
        *) bad "could not download $img: $reason" \
          "the Docker service cannot reach the registry: check the firewall, or the proxy settings of the Docker service" ;;
      esac
    fi
  done
  local build=(docker build -q) ca_dirs="components/nautobot/certs glue/certs" d
  if [ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]; then
    local p=${HTTPS_PROXY:-${https_proxy:-}}
    build+=(--network host --build-arg "HTTPS_PROXY=$p" --build-arg "https_proxy=$p"
            --build-arg "HTTP_PROXY=${HTTP_PROXY:-$p}" --build-arg "http_proxy=${HTTP_PROXY:-$p}")
  fi
  # prepare-server.sh --proxy-ca keeps the proxy's CA here, so nobody has to export CA_FILE by hand.
  if [ -z "${CA_FILE:-}" ] && [ -f /usr/local/share/ioh-msp/proxy-ca.pem ]; then CA_FILE=/usr/local/share/ioh-msp/proxy-ca.pem; fi
  if [ -n "${CA_FILE:-}" ]; then
    [ -f "$CA_FILE" ] || fail "CA_FILE=$CA_FILE does not exist" "export CA_FILE=/full/path/to/the/proxy-ca.crt"
    grep -q 'BEGIN CERTIFICATE' "$CA_FILE" || fail "CA_FILE=$CA_FILE holds no PEM certificate" "export the proxy's CA certificate in PEM (Base64) format"
    for d in $ca_dirs; do split_ca "$CA_FILE" "$d"; done
    info "trusting $(grep -c 'BEGIN CERTIFICATE' "$CA_FILE") certificate(s) from $CA_FILE during the builds"
  fi
  info "building Nautobot with Golden Config"
  if ! "${build[@]}" --build-arg NAUTOBOT_IMAGE="$NAUTOBOT_IMAGE" -t "$NAUTOBOT_TAG" components/nautobot >/dev/null; then
    for d in $ca_dirs; do rm -f "$d"/custom-ca-*.crt; done
    fail "the Nautobot image did not build" "check Internet access to pypi.org; behind a proxy export HTTPS_PROXY (and CA_FILE)"
  fi
  info "building msp-glue"
  if ! "${build[@]}" -t "$GLUE_TAG" glue >/dev/null; then
    for d in $ca_dirs; do rm -f "$d"/custom-ca-*.crt; done
    fail "the msp-glue image did not build" "check Internet access to pypi.org; behind a proxy export HTTPS_PROXY (and CA_FILE)"
  fi
  for d in $ca_dirs; do rm -f "$d"/custom-ca-*.crt; done
  local missing=0
  for img in $(dc config --images | sort -u); do
    docker image inspect "$img" >/dev/null 2>&1 || missing=$((missing + 1))
  done
  if [ "$missing" != 0 ] && [ "$rate_limited" = 1 ]; then
    fail "$missing image(s) missing: Docker Hub's limit for downloads without an account was reached" \
      "run docker login with a free Docker account (or wait a few hours), then run ./scripts/install.sh again"
  fi
  [ "$missing" = 0 ] || fail "$missing image(s) missing (see FAIL above)" \
    "make sure the Docker service can reach Docker Hub, codeberg.org and quay.io (behind a web proxy: sudo ./scripts/prepare-server.sh ... --proxy <proxy URL>), then run ./scripts/install.sh again"
}

stage_start() {
  step "Starting the platform (the first start takes 10 to 25 minutes)"
  load_env
  docker image inspect "$GLUE_TAG" >/dev/null 2>&1 || fail "the msp-glue image is missing" "./scripts/install.sh images"
  dc_up postgres mariadb valkey forgejo zabbix-server zabbix-web zabbix-proxy-central glpi semaphore nautobot ||
    fail "Docker could not start every container" "read the error above (for example a port already in use), then run ./scripts/install.sh again"
  wait_for "PostgreSQL, MariaDB and Valkey" 300 "docker compose logs --tail 50 postgres mariadb valkey" all_healthy postgres mariadb valkey
  wait_for "Nautobot (the first start applies about 500 database updates)" 1800 \
    "docker compose logs --tail 80 nautobot" health_is nautobot healthy
  wait_for "GLPI" 600 "docker compose logs --tail 50 glpi" probe http://glpi/
  wait_for "Zabbix" 600 "docker compose logs --tail 50 zabbix-web zabbix-server" probe http://zabbix-web:8080/
  wait_for "Semaphore" 300 "docker compose logs --tail 50 semaphore" probe http://semaphore:3000/api/ping
  wait_for "Forgejo" 300 "docker compose logs --tail 50 forgejo" probe http://forgejo:3000/api/healthz
  dc_up nautobot-worker nautobot-scheduler || fail "the Nautobot worker did not start" "docker compose logs --tail 50 nautobot-worker"
  wait_for "the Nautobot job worker" 900 "docker compose logs --tail 80 nautobot-worker" health_is nautobot-worker healthy
  ok "all tools are running"
}

stage_glpi_api() {
  step "GLPI API access (Setup > General > API)"
  load_env
  glpi_console config:set enable_api 1
  glpi_console config:set enable_api_login_credentials 1
  glpi_console config:set enable_api_login_external_token 1
  local subnet range gpw=glpi result
  subnet=$(docker network inspect msp -f '{{(index .IPAM.Config 0).Subnet}}')
  range=$(python3 -c "import ipaddress,sys; n=ipaddress.ip_network(sys.argv[1]); print(n.network_address, n.broadcast_address)" "$subnet")
  if is_done harden; then gpw=${GLPI_ADMIN_PASSWORD:-glpi}; fi
  local token=${GLPI_APP_TOKEN:-}
  local new_token=0
  if [ -z "$token" ]; then token=$(openssl rand -hex 20); new_token=1; fi
  export GPW=$gpw APP_TOKEN=$token NEW_TOKEN=$new_token RANGE_START=${range% *} RANGE_END=${range#* }
  result=$(dc exec -T -e GPW -e APP_TOKEN -e NEW_TOKEN -e RANGE_START -e RANGE_END glpi bash -s <<'EOS'
S=$(curl -s -u "glpi:$GPW" http://localhost/apirest.php/initSession | php -r '$j = json_decode(stream_get_contents(STDIN)); echo is_object($j) ? ($j->session_token ?? "") : "";')
if [ -z "$S" ]; then echo LOGIN_FAILED; exit 0; fi
ID=$(curl -s -H "Session-Token: $S" "http://localhost/apirest.php/APIClient?range=0-200" | php -r '$j = json_decode(stream_get_contents(STDIN), true); foreach ((is_array($j) ? $j : []) as $c) { if (($c["name"] ?? "") === "msp-platform") { echo $c["id"]; break; } }')
BODY="{\"input\":{\"name\":\"msp-platform\",\"is_active\":1,\"ipv4_range_start\":\"$RANGE_START\",\"ipv4_range_end\":\"$RANGE_END\",\"app_token\":\"$APP_TOKEN\",\"dolog_method\":0}}"
if [ -z "$ID" ]; then
  curl -s -X POST -H 'Content-Type: application/json' -H "Session-Token: $S" http://localhost/apirest.php/APIClient -d "$BODY" >/dev/null
  echo CREATED
elif [ "$NEW_TOKEN" = 1 ]; then
  curl -s -X PUT -H 'Content-Type: application/json' -H "Session-Token: $S" "http://localhost/apirest.php/APIClient/$ID" -d "$BODY" >/dev/null
  echo UPDATED
else
  echo EXISTS
fi
curl -s -H "Session-Token: $S" http://localhost/apirest.php/killSession >/dev/null
EOS
)
  unset GPW
  case "$result" in
    *LOGIN_FAILED*) fail "could not log in to GLPI's API as glpi" "if you changed the glpi password by hand, put it in GLPI_ADMIN_PASSWORD in .env and run ./scripts/install.sh harden first" ;;
    *CREATED* | *UPDATED* | *EXISTS*) ok "API client msp-platform for $subnet (${result})" ;;
    *) fail "unexpected answer from GLPI: $result" "docker compose logs --tail 50 glpi" ;;
  esac
  [ "$new_token" = 1 ] && set_env GLPI_APP_TOKEN "$token"
  # The application token must be accepted from the platform network: GLPI then only complains about the missing log-in.
  local answer
  answer=$(docker run --rm --network msp --entrypoint python "$GLUE_TAG" -c '
import sys, urllib.request, urllib.error
req = urllib.request.Request("http://glpi/apirest.php/initSession", headers={"App-Token": sys.argv[1]})
try:
    print(urllib.request.urlopen(req, timeout=10).read().decode()[:200])
except urllib.error.HTTPError as e:
    print(e.read().decode()[:200])' "$token" 2>&1 || true)
  case "$answer" in
    *ERROR_LOGIN_PARAMETERS_MISSING*) ok "application token accepted from the platform network" ;;
    *) fail "GLPI refused the application token from the platform network: $answer" \
         "in GLPI, Setup > General > API, check the client msp-platform (range $subnet); then run ./scripts/install.sh glpi-api" ;;
  esac
}

stage_forgejo_admin() {
  step "Forgejo administrator"
  load_env
  if dc exec -T -u git forgejo forgejo admin user list --admin 2>/dev/null | awk 'NR > 1 {print $2}' | grep -qx "$FORGEJO_ADMIN_USER"; then
    ok "administrator $FORGEJO_ADMIN_USER already exists"
  else
    dc exec -T -u git forgejo forgejo admin user create --admin --username "$FORGEJO_ADMIN_USER" \
      --password "$FORGEJO_ADMIN_PASSWORD" --email "${NAUTOBOT_SUPERUSER_EMAIL:-noc@example.internal}" \
      --must-change-password=false >/dev/null ||
      fail "could not create the Forgejo administrator" "docker compose logs --tail 50 forgejo"
    ok "administrator $FORGEJO_ADMIN_USER created (password FORGEJO_ADMIN_PASSWORD in .env)"
  fi
}

stage_tokens() {
  step "Service accounts and API tokens"
  load_env
  local zpw=zabbix gpw=glpi out
  if is_done harden; then zpw=${ZABBIX_ADMIN_PASSWORD:-zabbix}; gpw=${GLPI_ADMIN_PASSWORD:-glpi}; fi
  glpi_console config:set enable_api_login_credentials 1
  export BOOTSTRAP_ZABBIX_PASSWORD=$zpw BOOTSTRAP_GLPI_PASSWORD=$gpw
  export BOOTSTRAP_SEMAPHORE_PASSWORD=$SEMAPHORE_ADMIN_PASSWORD BOOTSTRAP_FORGEJO_PASSWORD=$FORGEJO_ADMIN_PASSWORD
  if ! out=$(dc run --rm --no-deps -e BOOTSTRAP_ZABBIX_PASSWORD -e BOOTSTRAP_GLPI_PASSWORD -e BOOTSTRAP_SEMAPHORE_PASSWORD \
      -e BOOTSTRAP_FORGEJO_PASSWORD msp-glue mspctl bootstrap tokens --forgejo-user "$FORGEJO_ADMIN_USER" 2>&1); then
    printf '%s\n' "$out" | tail -n 15
    fail "creating the service accounts failed" \
      "if you changed the Zabbix Admin or GLPI glpi password by hand, write it into ZABBIX_ADMIN_PASSWORD or GLPI_ADMIN_PASSWORD in .env and add a line 'harden' to .install-state"
  fi
  unset BOOTSTRAP_ZABBIX_PASSWORD BOOTSTRAP_GLPI_PASSWORD BOOTSTRAP_SEMAPHORE_PASSWORD BOOTSTRAP_FORGEJO_PASSWORD
  local line count=0
  while IFS= read -r line; do
    set_env "${line%%=*}" "${line#*=}"
    count=$((count + 1))
  done < <(printf '%s\n' "$out" | grep -E '^[A-Z_]+=')
  [ "$count" -ge 5 ] || fail "expected 5 tokens, got $count" "run ./scripts/install.sh tokens again; if it repeats, look at install.log"
  if is_done harden; then glpi_console config:set enable_api_login_credentials 0; fi
  load_env
  umask 022
  printf '%s' "$FORGEJO_ADMIN_USER" > secrets/platform/forgejo_username
  printf '%s' "$FORGEJO_API_TOKEN" > secrets/platform/forgejo_token
  chmod 644 secrets/platform/forgejo_username secrets/platform/forgejo_token
  ok "$count tokens written to .env"
  # New tokens mean the later stages must run again.
  unmark platform; unmark glue; unmark check
}

stage_platform() {
  step "Shared baseline in every tool (2 to 5 minutes)"
  load_env
  [ -n "${GLPI_ZABBIX_WEBHOOK_TOKEN:-}" ] || fail "GLPI_ZABBIX_WEBHOOK_TOKEN is empty in .env" "./scripts/install.sh tokens"
  export BOOTSTRAP_WEBHOOK_TOKEN=$GLPI_ZABBIX_WEBHOOK_TOKEN
  dc run --rm --no-deps -e BOOTSTRAP_WEBHOOK_TOKEN msp-glue mspctl bootstrap platform ||
    fail "the platform baseline did not finish" "read the error above; it names the tool. Then run ./scripts/install.sh again"
  unset BOOTSTRAP_WEBHOOK_TOKEN
  ok "Nautobot, Forgejo, Golden Config, GLPI and Zabbix baseline in place"
}

stage_glue() {
  step "Integration service msp-glue"
  dc_up msp-glue || fail "msp-glue did not start" "docker compose logs --tail 50 msp-glue"
  wait_for "msp-glue" 300 "docker compose logs --tail 50 msp-glue" health_is msp-glue healthy
}

stage_harden() {
  step "Replace default administrator passwords"
  load_env
  [ -n "${ZABBIX_ADMIN_PASSWORD:-}" ] && [ -n "${GLPI_ADMIN_PASSWORD:-}" ] ||
    fail "ZABBIX_ADMIN_PASSWORD or GLPI_ADMIN_PASSWORD is empty in .env" "put a long random password in each, then run ./scripts/install.sh harden"
  health_is msp-glue healthy || fail "msp-glue is not running" "./scripts/install.sh glue"
  glpi_console config:set enable_api_login_credentials 1
  export NEW_ZABBIX_ADMIN_PASSWORD=$ZABBIX_ADMIN_PASSWORD NEW_GLPI_ADMIN_PASSWORD=$GLPI_ADMIN_PASSWORD
  dc exec -T -e NEW_ZABBIX_ADMIN_PASSWORD -e NEW_GLPI_ADMIN_PASSWORD msp-glue mspctl bootstrap harden ||
    fail "replacing the passwords failed" "read the error above, then run ./scripts/install.sh harden"
  unset NEW_ZABBIX_ADMIN_PASSWORD NEW_GLPI_ADMIN_PASSWORD
  local code
  export GPW=$GLPI_ADMIN_PASSWORD
  code=$(dc exec -T -e GPW glpi bash -c 'curl -s -o /dev/null -w "%{http_code}" -u "glpi:$GPW" http://localhost/apirest.php/initSession')
  unset GPW
  [ "$code" = 200 ] || fail "GLPI did not accept the new glpi password (HTTP $code)" "./scripts/install.sh harden"
  glpi_console config:set enable_api_login_credentials 0
  ok "GLPI API log-in with a password switched off (msp-glue uses tokens)"
  info "Zabbix web log-in: Admin / ZABBIX_ADMIN_PASSWORD in .env"
  info "GLPI web log-in:   glpi / GLPI_ADMIN_PASSWORD in .env"
  info "Store both in your password manager now."
}

stage_check() {
  step "Final check"
  local out
  out=$(dc exec -T msp-glue mspctl check 2>&1) || true
  printf '%s\n' "$out"
  if printf '%s\n' "$out" | grep -q FAIL || ! printf '%s\n' "$out" | grep -q 'OK    Forgejo'; then
    fail "msp-glue cannot reach every tool" "docker compose logs --tail 50 msp-glue, fix the tool named FAIL, then run ./scripts/install.sh check"
  fi
  dc ps --format 'table {{.Service}}\t{{.Status}}'
}

stage_proxy() {
  step "Reverse proxy with HTTPS (Caddy)"
  load_env
  [ "${MSP_DOMAIN:-msp.example.internal}" != msp.example.internal ] || fail "MSP_DOMAIN is still the example" "./scripts/install.sh settings --domain <your domain>"
  add_profile proxy
  mkdir -p components/proxy/certs
  dc_up proxy || fail "the reverse proxy did not start" "read the message above (ports 80 and 443 must be free), then run ./scripts/install.sh proxy"
  wait_for "Caddy" 120 "docker compose logs --tail 50 proxy" health_is proxy running
  sleep 3
  local t code url
  for t in glpi nautobot zabbix semaphore git glue; do
    url="https://$t.$MSP_DOMAIN/"
    [ "$t" = glue ] && url="${url}healthz"
    code=$(curl -sk --noproxy '*' -o /dev/null -m 20 -w '%{http_code}' --resolve "$t.$MSP_DOMAIN:443:127.0.0.1" "$url" || true)
    case "$code" in
      2* | 3*) ok "$url answers ($code)" ;;
      403) ok "$url answers (403 here: this server is outside ADMIN_NETWORKS, as intended)" ;;
      *) bad "$url gave '$code'" "docker compose logs --tail 50 proxy" ;;
    esac
  done
  [ "$PROBLEMS" = 0 ] || fail "the reverse proxy does not answer for every tool" "docker compose logs --tail 50 proxy"
  if dc cp proxy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt >/dev/null 2>&1; then
    ok "Caddy's root certificate saved as $KIT_DIR/caddy-root.crt: install it on NOC PCs, or use IOH certificates (see the guide)"
  fi
  info "Each name must resolve to this server in DNS (or in the hosts file of your PC while testing)."
}

stage_grafana() {
  step "Customer dashboards (Grafana, phase 3)"
  load_env
  local dir=components/grafana/plugins arch
  case "$(uname -m)" in aarch64 | arm64) arch=arm64 ;; *) arch=amd64 ;; esac
  if [ ! -f "$dir/alexanderzobnin-zabbix-app/plugin.json" ]; then
    mkdir -p "$dir"
    curl -fsSL -o "$dir/zabbix.zip" \
      "https://grafana.com/api/plugins/alexanderzobnin-zabbix-app/versions/$GRAFANA_PLUGIN_VERSION/download?os=linux&arch=$arch" ||
      fail "could not download the Grafana Zabbix plugin" "check access to grafana.com (or HTTPS_PROXY), then run ./scripts/install.sh grafana"
    python3 -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$dir/zabbix.zip" "$dir"
    chmod +x "$dir"/alexanderzobnin-zabbix-app/datasource/gpx_zabbix-datasource_* 2>/dev/null || true
    rm -f "$dir/zabbix.zip"
  fi
  ok "Zabbix plugin $GRAFANA_PLUGIN_VERSION downloaded to $dir"
  add_profile grafana
  set_env GRAFANA_URL http://grafana:3000
  load_env
  docker pull -q "$GRAFANA_IMAGE" >/dev/null 2>&1 || docker image inspect "$GRAFANA_IMAGE" >/dev/null 2>&1 ||
    fail "the Grafana image could not be downloaded" "check the Docker service's Internet access or proxy"
  dc create grafana >/dev/null 2>&1 || fail "Docker could not create the Grafana container" "docker compose create grafana"
  # Put the plugin into Grafana's own data volume, so Grafana never has to reach grafana.com itself.
  docker run --rm --user 0 --entrypoint sh -v "${COMPOSE_PROJECT_NAME:-ioh-msp-stack}_grafana_data:/var/lib/grafana" \
    -v "$KIT_DIR/$dir:/src:ro" "$GLUE_TAG" -c 'mkdir -p /var/lib/grafana/plugins &&
      rm -rf /var/lib/grafana/plugins/alexanderzobnin-zabbix-app &&
      cp -r /src/alexanderzobnin-zabbix-app /var/lib/grafana/plugins/ &&
      chown -R 472:0 /var/lib/grafana/plugins' || fail "could not copy the plugin into Grafana's volume" "run ./scripts/install.sh grafana again"
  dc_up grafana || fail "Grafana did not start" "docker compose logs --tail 50 grafana"
  wait_for "Grafana" 300 "docker compose logs --tail 50 grafana" probe http://grafana:3000/api/health
  local answer
  answer=$(docker run --rm --network msp -e GRAFANA_ADMIN_PASSWORD --entrypoint python "$GLUE_TAG" -c '
import base64, json, os, urllib.request
auth = base64.b64encode(("admin:" + os.environ["GRAFANA_ADMIN_PASSWORD"]).encode()).decode()
req = urllib.request.Request("http://grafana:3000/api/plugins/alexanderzobnin-zabbix-app/settings",
                             headers={"Authorization": "Basic " + auth})
try:
    print(json.load(urllib.request.urlopen(req, timeout=10)).get("info", {}).get("version", "?"))
except Exception as e:
    print("missing", e)' 2>&1 || true)
  case "$answer" in
    "$GRAFANA_PLUGIN_VERSION"*) ok "Grafana runs the Zabbix plugin $answer" ;;
    *) fail "Grafana does not show the Zabbix plugin ($answer)" "docker compose logs --tail 50 grafana; then ./scripts/install.sh grafana" ;;
  esac
  dc_up msp-glue || fail "msp-glue did not restart" "docker compose logs --tail 50 msp-glue"
  wait_for "msp-glue" 300 "docker compose logs --tail 50 msp-glue" health_is msp-glue healthy
  dc exec -T msp-glue mspctl tenant sync --all || fail "creating the customers' Grafana organisations failed" "docker compose logs --tail 50 msp-glue"
  if is_done proxy; then dc_up proxy || true; fi
  ok "Grafana is on; every customer has its own organisation (https://grafana.$MSP_DOMAIN once the proxy runs)"
}

proxy_serves() {  # proxy_serves FINGERPRINT NAME...: true when Caddy answers every NAME with that certificate
  local want=$1 t got waited=0
  shift
  while [ "$waited" -lt 60 ]; do
    got=""
    for t in "$@"; do
      got=$(openssl s_client -connect 127.0.0.1:443 -servername "$t" </dev/null 2>/dev/null |
        openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 || true)
      [ "$got" = "$want" ] || break
    done
    [ "$got" = "$want" ] && return 0
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

stage_certs() {  # certs <fullchain.pem> <privkey.pem>, or certs internal
  step "Reverse proxy certificates"
  load_env
  is_done proxy || fail "the reverse proxy is not on yet" "./scripts/install.sh proxy"
  local dir=components/proxy/certs
  if [ "${1:-}" = internal ]; then
    set_env CADDY_TLS internal
    load_env
    dc_up proxy || fail "the reverse proxy did not restart" "docker compose logs --tail 50 proxy"
    wait_for "Caddy" 120 "docker compose logs --tail 50 proxy" health_is proxy running
    ok "the reverse proxy uses Caddy's own certificates again: NOC PCs need caddy-root.crt (./scripts/install.sh proxy saves it)"
    return 0
  fi
  local chain=${1:-} key=${2:-}
  [ -n "$chain" ] && [ -n "$key" ] || fail "name the certificate file and the key file" "./scripts/install.sh certs ~/fullchain.pem ~/privkey.pem"
  [ -r "$chain" ] || fail "cannot read $chain" "copy the certificate to the server and give its full path"
  [ -r "$key" ] || fail "cannot read $key" "copy the private key to the server and give its full path"

  # 1. The files themselves: PEM certificate, key without pass phrase, key and certificate belong together.
  local count
  count=$(grep -c 'BEGIN CERTIFICATE' "$chain" || true)
  [ "${count:-0}" -ge 1 ] && openssl x509 -in "$chain" -noout 2>/dev/null ||
    fail "$chain holds no PEM certificate" "ask for PEM (Base64) format; a binary .cer or .der file converts with: openssl x509 -inform der -in cert.cer -out fullchain.pem"
  if ! openssl pkey -in "$key" -passin pass: -noout 2>/dev/null; then
    if grep -q ENCRYPTED "$key"; then
      fail "the private key is protected by a pass phrase, which Caddy cannot type" "openssl pkey -in $key -out ~/privkey-open.pem (type the pass phrase once), then use ~/privkey-open.pem"
    fi
    fail "$key is not a private key in PEM format" "ask for the key in PEM format (it starts with -----BEGIN PRIVATE KEY-----)"
  fi
  [ "$(openssl x509 -in "$chain" -noout -pubkey | openssl sha256)" = "$(openssl pkey -in "$key" -pubout | openssl sha256)" ] ||
    fail "the private key does not belong to this certificate" "use the key that was created together with this certificate's request"
  ok "the private key belongs to the certificate"
  local until
  until=$(openssl x509 -in "$chain" -noout -enddate | cut -d= -f2)
  openssl x509 -in "$chain" -noout -checkend 0 >/dev/null || fail "the certificate expired on $until" "ask IOH's certificate authority for a new one"
  if openssl x509 -in "$chain" -noout -checkend 2592000 >/dev/null; then ok "valid until $until"
  else warn "the certificate expires within 30 days ($until)" "order the renewal now"; fi
  if [ "$count" -ge 2 ]; then ok "$((count - 1)) intermediate certificate(s) included"
  else warn "the file holds only the server certificate" "if IOH's certificate authority uses an intermediate certificate, append it: cat server.pem intermediate.pem > fullchain.pem"; fi

  # 2. Every name the proxy serves (Grafana and single sign-on only once they are on).
  local t
  for t in glpi nautobot zabbix semaphore git glue grafana sso; do
    if openssl x509 -in "$chain" -noout -checkhost "$t.$MSP_DOMAIN" 2>/dev/null | grep -q 'does match'; then
      ok "covers $t.$MSP_DOMAIN"
    elif { [ "$t" = grafana ] && [[ ",${COMPOSE_PROFILES:-}," != *,grafana,* ]]; } ||
         { [ "$t" = sso ] && [[ ",${COMPOSE_PROFILES:-}," != *,sso,* ]]; }; then
      warn "does not cover $t.$MSP_DOMAIN (needed once that component is on)" "a certificate for *.$MSP_DOMAIN covers every name"
    else
      bad "does not cover $t.$MSP_DOMAIN"
    fi
  done
  [ "$PROBLEMS" = 0 ] || fail "the certificate does not cover every name in use" "ask for a certificate for *.$MSP_DOMAIN, or one listing every name above"

  # 3. Switch, prove every name serves it, and put the previous certificates back if not.
  local fingerprint previous=${CADDY_TLS:-internal} saved
  fingerprint=$(openssl x509 -in "$chain" -noout -fingerprint -sha256 | cut -d= -f2)
  saved=$(mktemp -d)
  cp -p "$dir"/fullchain.pem "$dir"/privkey.pem "$saved"/ 2>/dev/null || true
  mkdir -p "$dir"
  [ "$(realpath "$chain")" = "$(realpath -m "$dir/fullchain.pem")" ] || cp "$chain" "$dir/fullchain.pem"
  [ "$(realpath "$key")" = "$(realpath -m "$dir/privkey.pem")" ] || cp "$key" "$dir/privkey.pem"
  chmod 644 "$dir/fullchain.pem"
  chmod 600 "$dir/privkey.pem"
  set_env CADDY_TLS "/certs/fullchain.pem /certs/privkey.pem"
  load_env  # docker compose prefers the exported variable over .env, so refresh it
  if dc_up proxy && proxy_serves "$fingerprint" glpi.$MSP_DOMAIN nautobot.$MSP_DOMAIN zabbix.$MSP_DOMAIN \
       semaphore.$MSP_DOMAIN git.$MSP_DOMAIN glue.$MSP_DOMAIN; then
    rm -rf "$saved"
    ok "every name now answers with the IOH certificate (SHA-256 ${fingerprint:0:23}...)"
    info "Browsers on NOC PCs trust it when they trust IOH's certificate authority; caddy-root.crt is no longer needed."
    return 0
  fi
  printf '%s\n' "$(docker compose logs --tail 8 proxy 2>&1)"
  rm -f "$dir/fullchain.pem" "$dir/privkey.pem"
  cp -p "$saved"/*.pem "$dir"/ 2>/dev/null || true
  rm -rf "$saved"
  set_env CADDY_TLS "$previous"
  load_env
  dc_up proxy || true
  fail "Caddy did not serve the new certificate, so the previous certificates are back in place" \
    "the last proxy log line above gives Caddy's reason (Caddy accepts RSA, ECDSA P-256 or P-384, and Ed25519 keys)"
}

# ======================================================================= driver
usage() { sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; }

show_status() {
  step "Installation status ($KIT_DIR)"
  local s tls
  for s in "${ALL_STAGES[@]}"; do
    if is_done "$s"; then printf '  %sdone%s   %-14s %s\n' "$G" "$N" "$s" "$(grep -- "^$s"$'\t' "$STATE_FILE" | cut -f2)"
    else printf '  ....   %s\n' "$s"; fi
  done
  if is_done proxy; then
    tls=$( (load_env; echo "${CADDY_TLS:-internal}") )
    if [ "$tls" = internal ]; then info "certificates: Caddy's own (IOH certificates: ./scripts/install.sh certs <fullchain.pem> <privkey.pem>)"
    else info "certificates: IOH certificates in components/proxy/certs"; fi
  fi
}

run_stage() {
  CURRENT_STAGE=$1
  shift
  "stage_${CURRENT_STAGE//-/_}" "$@"
  mark_done "$CURRENT_STAGE"
  CURRENT_STAGE=""
}

run_all() {
  local s
  for s in "${CORE_STAGES[@]}"; do
    if is_done "$s"; then printf '  %sdone%s   %s\n' "$G" "$N" "$s"; continue; fi
    if [ "$s" = settings ]; then
      CURRENT_STAGE=settings
      fail "the settings are not done yet" \
        "./scripts/install.sh settings --domain <your domain> --subnet <range> --admin-networks \"<NOC and VPN ranges>\" --tz Asia/Jakarta"
    fi
    run_stage "$s"
  done
  step "Core installation complete"
  info "Next: ./scripts/install.sh proxy   (HTTPS on https://<tool>.$( (load_env; echo "$MSP_DOMAIN") ), once the DNS names exist)"
  info "Then: change nothing else by hand; onboard the first customer as the guide shows."
}

main() {
  case "${1:-}" in help | -h | --help) usage; return 0 ;; esac
  RUN_CMD="./scripts/install.sh${*:+ $*}"
  check_account
  touch "$LOG_FILE" && chmod 600 "$LOG_FILE"
  exec > >(tee -a "$LOG_FILE") 2>&1
  printf '\n%s ./scripts/install.sh %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
  local cmd=${1:-all}
  case "$cmd" in
    status) show_status ;;
    all) run_all ;;
    preflight | settings | network | images | start | glpi-api | forgejo-admin | tokens | platform | glue | harden | check | proxy | grafana)
      shift
      run_stage "$cmd" "$@"
      ;;
    certs)
      # A switch rather than a stage: it is not recorded in .install-state, status shows the current choice.
      shift
      CURRENT_STAGE=certs
      stage_certs "$@"
      CURRENT_STAGE=""
      ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
