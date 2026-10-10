#!/usr/bin/env bash
# =============================================================================
# Prepare an Ubuntu 24.04 (or 22.04) server for the IOH open-source managed services platform.
# Run it once with sudo, before ./scripts/install.sh. Running it again is safe: it only changes
# what differs, and restarts Docker only when Docker's own settings change.
#
#   sudo ./scripts/prepare-server.sh --admin-networks "10.10.0.0/16 10.20.0.0/16"
#
# Options:
#   --admin-networks "CIDR ..."  NOC and VPN ranges allowed to reach SSH (required for the firewall)
#   --pool CIDR                  Docker's address pool, a /16 to /20 that no managed network uses
#                                (default 172.31.0.0/16)
#   --tz ZONE                    time zone (default Asia/Jakarta)
#   --proxy URL                  IOH web proxy for the shell, apt and Docker, for example
#                                http://proxy.ioh.internal:8080 (default: none, direct Internet)
#   --proxy-ca FILE              the proxy's CA certificate in PEM format, if the proxy inspects TLS
#   --no-firewall                leave the firewall as it is
#   --no-upgrade                 install the tools but skip "apt-get upgrade"
#
# What it does, in order: web proxy (only with --proxy); Ubuntu updates and basic tools; time zone
# and time synchronisation; Docker Engine with Compose; your user in the docker group; Docker's
# proxy, address pool and log rotation; the firewall (SSH from your current address and the admin
# networks, then 80, 443 and 10051). Everything it prints also goes to /var/log/ioh-msp-prepare.log.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=/var/log/ioh-msp-prepare.log
CA_STORE=/usr/local/share/ioh-msp/proxy-ca.pem   # read by install.sh for its two image builds

# ----------------------------------------------------------------------------- output
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; Y=; R=; N=; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %sOK%s     %s\n' "$G" "$N" "$*"; }
info() { printf '         %s\n' "$*"; }
warn() { printf '  %sWARN%s   %s\n' "$Y" "$N" "$1"; [ -n "${2:-}" ] && printf '         Fix: %s\n' "$2"; return 0; }
FAIL_SHOWN=""
fail() {
  printf '\n  %sSTOPPED%s %s\n' "$R" "$N" "$1"
  [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2"
  printf '  Then run the same command again: finished parts are skipped.\n'
  FAIL_SHOWN=1
  exit 1
}
on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ -z "$FAIL_SHOWN" ]; then
    printf '\n  %sSTOPPED%s an unexpected error (exit code %s). The lines above show the cause;\n' "$R" "$N" "$rc"
    printf '  the whole run is in %s. Fix the cause, then run the same command again.\n' "$LOG"
  fi
}
trap on_exit EXIT

usage() { sed -n '3,23p' "$0" | sed 's/^# \{0,1\}//'; }

# ------------------------------------------------------------------------- arguments
if [ "$(id -u)" != 0 ]; then
  FAIL_SHOWN=1
  printf 'Run it with sudo:  sudo %s %s\n' "$0" "$*"
  exit 1
fi
ADMIN_NETWORKS="" POOL=172.31.0.0/16 TZ_NAME=Asia/Jakarta PROXY="" PROXY_CA="" FIREWALL=1 UPGRADE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --admin-networks) ADMIN_NETWORKS=${2:-}; shift 2 ;;
    --pool) POOL=${2:-}; shift 2 ;;
    --tz) TZ_NAME=${2:-}; shift 2 ;;
    --proxy) PROXY=${2:-}; shift 2 ;;
    --proxy-ca) PROXY_CA=${2:-}; shift 2 ;;
    --no-firewall) FIREWALL=0; shift ;;
    --no-upgrade) UPGRADE=0; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage; FAIL_SHOWN=1; printf '\nUnknown option: %s\n' "$1"; exit 1 ;;
  esac
done

touch "$LOG" && chmod 600 "$LOG"
exec > >(tee -a "$LOG") 2>&1
printf '\n%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$0"

TARGET_USER=${SUDO_USER:-}
[ "$TARGET_USER" = root ] && TARGET_USER=""

export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
APT=(apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
DOCKER_RESTART=0

# ============================================================================ checks
step "Checks (nothing is changed yet)"
os_id=$( (. /etc/os-release 2>/dev/null; echo "${ID:-}-${VERSION_ID:-}") )
os_name=$( (. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}") )
case "$os_id" in
  ubuntu-24.04 | ubuntu-22.04) ok "Operating system: $os_name" ;;
  ubuntu-*) warn "Operating system: $os_name (the kit was tested on Ubuntu 24.04)" ;;
  *) fail "this script is for Ubuntu; this server runs $os_name" "use an Ubuntu Server 24.04 virtual machine" ;;
esac
if [ -n "$TARGET_USER" ]; then ok "your account: $TARGET_USER (it joins the docker group)"
else warn "running as root directly" "a personal account with sudo is safer; the platform works either way"; fi

python3 - "$ADMIN_NETWORKS" "$POOL" "$TZ_NAME" "$PROXY" <<'PY' || fail "a setting is not valid (see above)" "correct it and run the command again"
import ipaddress, os, re, sys
nets, pool, tz, proxy = sys.argv[1:5]
errors = []
for n in nets.split():
    try:
        ipaddress.ip_network(n, strict=False)
    except ValueError:
        errors.append(f"--admin-networks: '{n}' is not a range such as 10.10.0.0/16")
try:
    p = ipaddress.ip_network(pool, strict=True)
    if not 16 <= p.prefixlen <= 20:
        errors.append(f"--pool {pool}: use a /16 to /20 so Docker has room for several networks")
    if p.overlaps(ipaddress.ip_network("172.30.0.0/24")):
        errors.append(f"--pool {pool} contains 172.30.0.0/24, the platform network the installer uses by default")
except ValueError:
    errors.append(f"--pool '{pool}' is not a network address such as 172.31.0.0/16")
if not os.path.exists(f"/usr/share/zoneinfo/{tz}"):
    errors.append(f"--tz '{tz}' is not a known time zone, for example Asia/Jakarta")
if proxy and not re.fullmatch(r"https?://[^\s/]+(:\d+)?/?", proxy):
    errors.append(f"--proxy '{proxy}' is not a proxy address such as http://proxy.ioh.internal:8080")
for e in errors:
    print("  FAIL   " + e)
sys.exit(1 if errors else 0)
PY
ok "settings: pool $POOL, time zone $TZ_NAME${PROXY:+, proxy $PROXY}${ADMIN_NETWORKS:+, admin networks $ADMIN_NETWORKS}"

# Docker's pool must not hide a network this server already reaches (Docker's own bridges excepted).
overlaps=$(ip -4 route show 2>/dev/null | python3 -c '
import ipaddress, sys
pool = ipaddress.ip_network(sys.argv[1])
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
    if net.overlaps(pool):
        print(line.strip())' "$POOL")
[ -z "$overlaps" ] || fail "the pool $POOL overlaps a network this server reaches: $(echo "$overlaps" | head -n 1)" \
  "choose another range with --pool, one that no managed or IOH network uses"
ok "pool $POOL overlaps no route on this server"

if [ "$FIREWALL" = 1 ]; then
  [ -n "$ADMIN_NETWORKS" ] || fail "--admin-networks is missing; without it the firewall would allow SSH only from your current address" \
    "add --admin-networks \"<NOC and VPN ranges>\", or --no-firewall to leave the firewall alone"
fi
if [ -n "$PROXY_CA" ]; then
  [ -n "$PROXY" ] || fail "--proxy-ca needs --proxy as well" "add --proxy http://<proxy>:<port>"
  [ -r "$PROXY_CA" ] && grep -q 'BEGIN CERTIFICATE' "$PROXY_CA" ||
    fail "$PROXY_CA is not a readable PEM certificate" "copy the proxy's CA certificate to the server in PEM (Base64) format and give its full path"
fi

# ========================================================================== web proxy
if [ -n "$PROXY" ]; then
  step "Web proxy for the shell, apt and Docker"
  PROXY=${PROXY%/}
  if [ -n "$PROXY_CA" ]; then
    mkdir -p "$(dirname "$CA_STORE")"
    changed=0
    if ! cmp -s "$PROXY_CA" "$CA_STORE" 2>/dev/null; then cp "$PROXY_CA" "$CA_STORE"; chmod 644 "$CA_STORE"; changed=1; fi
    # update-ca-certificates wants one certificate per file.
    rm -f /usr/local/share/ca-certificates/ioh-proxy-*.crt
    awk '/-----BEGIN CERTIFICATE-----/ {n++; f = sprintf("/usr/local/share/ca-certificates/ioh-proxy-%d.crt", n)} n && f {print > f} /-----END CERTIFICATE-----/ {close(f); f = ""}' "$CA_STORE"
    update-ca-certificates >/dev/null 2>&1 || fail "update-ca-certificates failed" "check that $PROXY_CA is a PEM certificate"
    ok "proxy CA trusted ($(grep -c 'BEGIN CERTIFICATE' "$CA_STORE") certificate(s), kept in $CA_STORE for the installer)"
    [ "$changed" = 1 ] && DOCKER_RESTART=1
  fi
  no_proxy_list="localhost,127.0.0.1"
  python3 - "$PROXY" "$no_proxy_list" <<'PY'
import pathlib, sys
proxy, no_proxy = sys.argv[1:3]
path = pathlib.Path("/etc/environment")
keep = [l for l in (path.read_text().splitlines() if path.exists() else [])
        if l.split("=", 1)[0].strip().lower() not in ("http_proxy", "https_proxy", "no_proxy")]
keep += [f"http_proxy={proxy}", f"https_proxy={proxy}", f"HTTP_PROXY={proxy}", f"HTTPS_PROXY={proxy}",
         f"no_proxy={no_proxy}", f"NO_PROXY={no_proxy}"]
path.write_text("\n".join(keep) + "\n")
PY
  printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$PROXY" "$PROXY" > /etc/apt/apt.conf.d/95proxy
  export http_proxy=$PROXY https_proxy=$PROXY HTTP_PROXY=$PROXY HTTPS_PROXY=$PROXY no_proxy=$no_proxy_list NO_PROXY=$no_proxy_list
  ok "proxy set for every log-in (/etc/environment) and for apt"
  code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' https://registry-1.docker.io/v2/ 2>/dev/null || true)
  case "$code" in
    401 | 200) ok "Docker Hub answers through the proxy ($code)" ;;
    *) fail "Docker Hub does not answer through the proxy (HTTP ${code:-none})" \
         "curl -v https://registry-1.docker.io/v2/ shows why: a TLS error means the proxy needs --proxy-ca; 403 or 407 means the proxy refuses this server" ;;
  esac
fi

# ===================================================================== Ubuntu packages
step "Ubuntu updates and basic tools (a few minutes)"
"${APT[@]}" update >/dev/null || fail "apt could not read the package lists" \
  "the server must reach its Ubuntu mirror (outbound HTTP, port 80), or pass --proxy"
if [ "$UPGRADE" = 1 ]; then
  "${APT[@]}" upgrade >/dev/null || fail "apt-get upgrade failed" "the lines above name the package; run the command again"
  ok "Ubuntu packages up to date"
fi
"${APT[@]}" install ca-certificates curl unzip tmux chrony ufw python3 openssl iproute2 >/dev/null ||
  fail "installing the basic tools failed" "the lines above name the package; run the command again"
ok "basic tools installed: curl unzip tmux chrony ufw python3 openssl iproute2"
[ -f /var/run/reboot-required ] && warn "Ubuntu asks for a reboot after these updates" "sudo reboot when this script has finished, then log in again"

# ============================================================================== time
step "Time zone and time synchronisation"
timedatectl set-timezone "$TZ_NAME"
systemctl enable --now chrony >/dev/null 2>&1 || true
synced=no
for _ in $(seq 1 12); do
  synced=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo no)
  [ "$synced" = yes ] && break
  sleep 5
done
ok "time zone $TZ_NAME"
if [ "$synced" = yes ]; then ok "clock synchronised"
else warn "clock not synchronised yet" "if Internet time servers are blocked, put IOH's time servers in /etc/chrony/chrony.conf (server <address> iburst) and run: sudo systemctl restart chrony"; fi

# ============================================================================ Docker
step "Docker Engine and Compose"
docker_up() {  # wait up to a minute for the Docker service to answer
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && return 0; sleep 2; done
  return 1
}
if command -v docker >/dev/null && docker version >/dev/null 2>&1; then
  ok "Docker Engine $(docker version -f '{{.Server.Version}}') already installed"
else
  source_name="Docker's packages"
  info "installing Docker from get.docker.com (Docker's own packages, 1 to 3 minutes)"
  if ! { curl -fsSL -m 60 https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh >/var/log/ioh-msp-get-docker.log 2>&1; }; then
    warn "get.docker.com did not work (details in /var/log/ioh-msp-get-docker.log); installing Ubuntu's own Docker packages instead"
    "${APT[@]}" install docker.io docker-compose-v2 >/dev/null ||
      fail "Docker could not be installed" "allow get.docker.com and download.docker.com (or the Ubuntu mirror) through the firewall or proxy"
    source_name="Ubuntu's packages"
  fi
  rm -f /tmp/get-docker.sh
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker_up || fail "Docker was installed but does not start" "sudo journalctl -u docker --no-pager | tail -n 20 shows why"
  ok "Docker Engine $(docker version -f '{{.Server.Version}}') installed from $source_name"
fi
systemctl enable --now docker >/dev/null 2>&1 || true
compose=$(docker compose version --short 2>/dev/null | sed 's/^v//' || true)
if [ -z "$compose" ]; then
  if dpkg -s docker-ce >/dev/null 2>&1; then "${APT[@]}" install docker-compose-plugin >/dev/null || true
  else "${APT[@]}" install docker-compose-v2 >/dev/null || true; fi
  compose=$(docker compose version --short 2>/dev/null | sed 's/^v//' || true)
fi
[ -n "$compose" ] || fail "the Docker Compose plugin is missing" "sudo apt-get install -y docker-compose-plugin (or docker-compose-v2)"
if [ "$(printf '%s\n%s\n' 2.20 "$compose" | sort -V | head -n 1)" = 2.20 ]; then ok "Docker Compose $compose"
else fail "Docker Compose $compose is too old (2.20 or later needed)" "sudo apt-get install --only-upgrade docker-compose-plugin"; fi
# Buildx (the current image builder); without it builds still work but print a DEPRECATED notice.
if ! docker buildx version >/dev/null 2>&1; then
  if dpkg -s docker-ce >/dev/null 2>&1; then "${APT[@]}" install docker-buildx-plugin >/dev/null 2>&1 || true
  else "${APT[@]}" install docker-buildx >/dev/null 2>&1 || true; fi
fi
if docker buildx version >/dev/null 2>&1; then ok "Docker Buildx $(docker buildx version | awk '{print $2}')"
else warn "Docker Buildx is missing: image builds use the older builder and print a DEPRECATED notice, which is harmless"; fi

NEED_RELOGIN=0
if [ -n "$TARGET_USER" ]; then
  if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx docker; then ok "$TARGET_USER is in the docker group"
  else usermod -aG docker "$TARGET_USER"; NEED_RELOGIN=1; ok "$TARGET_USER added to the docker group (applies at the next log-in)"; fi
fi

# ===================================================================== Docker settings
step "Docker settings: proxy, address pool, log rotation"
if [ -n "$PROXY" ]; then
  mkdir -p /etc/systemd/system/docker.service.d
  dropin=/etc/systemd/system/docker.service.d/http-proxy.conf
  want=$(printf '[Service]\nEnvironment="HTTP_PROXY=%s"\nEnvironment="HTTPS_PROXY=%s"\nEnvironment="NO_PROXY=localhost,127.0.0.1"\n' "$PROXY" "$PROXY")
  if [ "$(cat "$dropin" 2>/dev/null)" != "$want" ]; then
    printf '%s\n' "$want" > "$dropin"
    systemctl daemon-reload
    DOCKER_RESTART=1
  fi
  ok "Docker downloads images through $PROXY"
fi
changed=$(python3 - "$POOL" <<'PY'
import ipaddress, json, os, sys
pool = ipaddress.ip_network(sys.argv[1])
bip = f"{pool.network_address + pool.num_addresses - 256 + 1}/24"   # first address of the pool's last /24
path = "/etc/docker/daemon.json"
cfg = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    cfg = json.load(open(path))
before = json.dumps(cfg, sort_keys=True)
cfg.update({"bip": bip, "default-address-pools": [{"base": str(pool), "size": 24}],
            "log-driver": "json-file", "log-opts": {"max-size": "50m", "max-file": "5"}})
if json.dumps(cfg, sort_keys=True) != before:
    os.makedirs("/etc/docker", exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print("yes")
else:
    print("no")
PY
) || fail "/etc/docker/daemon.json has a typing error, so nothing was changed" "show it with: sudo cat /etc/docker/daemon.json, correct it, and run the command again"
[ "$changed" = yes ] && DOCKER_RESTART=1
if [ "$DOCKER_RESTART" = 1 ]; then
  info "restarting Docker to apply the new settings"
  systemctl restart docker || fail "Docker did not restart with the new settings" \
    "sudo journalctl -u docker --no-pager | tail -n 20 names the setting; correct /etc/docker/daemon.json"
fi
docker_up || fail "Docker is not running" "sudo journalctl -u docker --no-pager | tail -n 20 shows why"
docker network rm ioh-pooltest >/dev/null 2>&1 || true
got=$(docker network create ioh-pooltest >/dev/null && docker network inspect ioh-pooltest -f '{{(index .IPAM.Config 0).Subnet}}' || true)
docker network rm ioh-pooltest >/dev/null 2>&1 || true
if python3 -c 'import ipaddress, sys; sys.exit(0 if ipaddress.ip_network(sys.argv[1]).subnet_of(ipaddress.ip_network(sys.argv[2])) else 1)' "${got:-0.0.0.0/32}" "$POOL" 2>/dev/null; then
  ok "new Docker networks come from $POOL (test network got $got)"
else
  fail "a test network got ${got:-no address} instead of an address from $POOL" "sudo cat /etc/docker/daemon.json; then sudo systemctl restart docker"
fi
ok "container logs capped at 5 files of 50 MB"

# ========================================================================== firewall
if [ "$FIREWALL" = 1 ]; then
  step "Firewall"
  # SSH port(s): from sshd's own settings, else from systemd's ssh.socket (Ubuntu 24.04), else 22.
  ssh_ports=$( (sshd -T 2>/dev/null || true) | awk '$1 == "port" {print $2}' | sort -u | tr '\n' ' ')
  if [ -z "${ssh_ports// /}" ]; then
    ssh_ports=$( (systemctl show -p Listen ssh.socket 2>/dev/null || true) | grep -o ':[0-9]* ' | tr -d ': ' | sort -u | tr '\n' ' ')
  fi
  [ -n "${ssh_ports// /}" ] || ssh_ports="22 "
  # Addresses with an SSH session open right now (yours included), so the firewall never locks them out.
  peers=$(ss -Htn state established 2>/dev/null | awk '{print $3, $4}' | python3 -c '
import ipaddress, sys
ports = set(sys.argv[1].split())
seen = []
for line in sys.stdin:
    try:
        local, peer = line.split()
        lport = local.rsplit(":", 1)[1]
        addr = peer.rsplit(":", 1)[0].strip("[]")
        if addr.startswith("::ffff:"):
            addr = addr[7:]
        ip = ipaddress.ip_address(addr)
    except (ValueError, IndexError):
        continue
    if lport in ports and not ip.is_loopback and str(ip) not in seen:
        seen.append(str(ip))
print(" ".join(seen))' "$ssh_ports" || true)
  for port in $ssh_ports; do
    for src in $peers $ADMIN_NETWORKS; do ufw allow from "$src" to any port "$port" proto tcp >/dev/null; done
  done
  if [ -n "$peers" ]; then ok "SSH (port ${ssh_ports% }) allowed from the address(es) logged in now: $peers"
  else info "no SSH session found (console log-in?); SSH is allowed from the admin networks only"; fi
  ok "SSH (port ${ssh_ports% }) allowed from $ADMIN_NETWORKS"
  for p in 80 443 10051; do ufw allow "$p/tcp" >/dev/null; done
  ok "80 and 443 (web interfaces) and 10051 (customer-site collectors) open"
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw --force enable >/dev/null || fail "the firewall could not be switched on" "sudo ufw status verbose shows why"
  ok "firewall on: everything else inbound is blocked"
  info "Keep this SSH session open and log in from a second window to prove SSH still works."
fi

# ============================================================================== done
step "Server ready"
if [ "$NEED_RELOGIN" = 1 ]; then
  info "Log out and log in again (your docker group membership applies to new log-ins), then:"
else
  info "Next:"
fi
info "  cd $(pwd) && ./scripts/install.sh preflight"
