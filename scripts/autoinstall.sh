#!/usr/bin/env bash
# =============================================================================
# Automatic install of the IOH open-source managed services platform: from an empty Ubuntu 24.04
# server to a working platform, with no questions on the way. Runs as root. On Google Cloud and AWS,
# "create.sh --autoinstall <settings file>" starts it on the new server by itself; on a VPS or any
# other Ubuntu server, the script that environments/bootstrap.sh makes does the same.
#
#   sudo ./scripts/autoinstall.sh start    install in the background (service ioh-msp-autoinstall)
#   sudo ./scripts/autoinstall.sh status   how far it is; once finished, the first sign-in
#   sudo ./scripts/autoinstall.sh tunnel   connect the Cloudflare Tunnel now (asks for its token)
#   sudo ./scripts/autoinstall.sh run      install in this terminal (what the service runs)
#
# Settings: /etc/ioh-msp/autoinstall.conf, or --config FILE (environments/autoinstall.conf.example
# explains every value). Progress goes to /var/log/ioh-msp-autoinstall.log. A step that fails stops
# the install with its reason and fix; "start" again continues with that step. Finished steps are
# not repeated, so running it again is safe.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
KIT_DIR=$(pwd)
CONF=/etc/ioh-msp/autoinstall.conf
VAR_DIR=/var/lib/ioh-msp
STATE=$VAR_DIR/autoinstall.state     # finished steps, one per line
STATUS=$VAR_DIR/autoinstall.status   # for "status" and the log-in banner: never a secret
DONE=$VAR_DIR/autoinstall.done       # the service starts only while this file is missing
PASSWORD_FILE=$VAR_DIR/first-password
SUMMARY=$VAR_DIR/first-sign-in.txt   # addresses and the first one-time password: root only
LOG=/var/log/ioh-msp-autoinstall.log
UNIT=ioh-msp-autoinstall
STEPS=(apt account kit prepare settings dockerhub core proxy tunnel grafana sso console ssotools samples backup)
ALWAYS="apt account kit prepare settings"   # cheap and safe to repeat, so a run after a fix repeats them
VERSION=$(cat VERSION 2>/dev/null || echo "?")

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; Y=; R=; N=; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %sOK%s     %s\n' "$G" "$N" "$*"; }
info() { printf '         %s\n' "$*"; }
warn() { printf '  %sWARN%s   %s\n' "$Y" "$N" "$1"; [ -n "${2:-}" ] && printf '         Fix: %s\n' "$2"; return 0; }
CURRENT=""   # "N of M: title" of the step running now
fail() {
  printf '\n  %sSTOPPED%s %s\n' "$R" "$N" "$1"
  [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2"
  if [ -n "$CURRENT" ]; then
    set_status stopped "$CURRENT" "$1"
    printf '  Then:   sudo %s/scripts/autoinstall.sh start   (continues with this step)\n' "$KIT_DIR"
  fi
  exit 1
}
usage() { sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'; }

set_status() {  # set_status running|stopped|finished "N of M: title" TEXT
  mkdir -p "$VAR_DIR"
  {
    printf 'state=%s\n' "$1"
    printf 'step=%s\n' "$(printf '%s' "$2" | tr -d '\n\r' | cut -c1-120)"
    printf 'text=%s\n' "$(printf '%s' "$3" | tr -d '\n\r' | cut -c1-200)"
    printf 'since=%s\n' "$(date '+%Y-%m-%d %H:%M %Z')"
  } > "$STATUS.tmp"
  chmod 644 "$STATUS.tmp"
  mv "$STATUS.tmp" "$STATUS"
}
status_value() { sed -n "s/^$1=//p" "$STATUS" 2>/dev/null || true; }
step_done() { [ -f "$STATE" ] && grep -qx "$1" "$STATE"; }
mark_done() { step_done "$1" || printf '%s\n' "$1" >> "$STATE"; }

title_of() {
  case "$1" in
    apt) echo "Ubuntu's own updates finish first" ;;
    account) echo "the platform's account, $INSTALL_USER" ;;
    kit) echo "the kit in $KIT_DIR" ;;
    prepare) echo "server preparation: updates, Docker, firewall (5 to 10 minutes)" ;;
    settings) echo "the installer's settings" ;;
    dockerhub) echo "Docker Hub log-in" ;;
    core) echo "the tools and their integration (20 to 45 minutes)" ;;
    proxy) echo "HTTPS for every tool" ;;
    tunnel) echo "the Cloudflare Tunnel" ;;
    grafana) echo "customer dashboards (Grafana)" ;;
    sso) echo "staff sign-in (Keycloak)" ;;
    console) echo "the MSP Console" ;;
    ssotools) echo "staff sign-in to the tools" ;;
    samples) echo "sample customers to practise on (5 to 10 minutes)" ;;
    backup) echo "nightly backup" ;;
  esac
}
enabled() {
  case "$1" in
    dockerhub) [ -n "$DOCKERHUB_USER" ] ;;
    tunnel) [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ] ;;
    grafana) [ "$GRAFANA" = yes ] ;;
    ssotools) [ "$SSO_TOOLS" = yes ] ;;
    samples) [ "$SAMPLES" = yes ] ;;
    backup) [ "$BACKUP" = yes ] ;;
    *) return 0 ;;
  esac
}

# ------------------------------------------------------------------ settings
load_conf() {  # the settings, read and checked by scripts/autoinstall-settings.py, never run as code
  [ -r "$CONF" ] || fail "the settings file $CONF is missing" \
    "copy environments/autoinstall.conf.example to $CONF, fill it in, then: sudo $KIT_DIR/scripts/autoinstall.sh start"
  chmod 600 "$CONF"
  ENVIRONMENT="" DOMAIN="" STAFF_EMAIL="" STAFF_NAME="" NOC_EMAIL="" TIMEZONE="" SUBNET="" POOL=""
  SSH_FROM="" ADMIN_NETWORKS="" PROXY="" PROXY_CA="" CLOUDFLARE_TUNNEL_TOKEN="" SAMPLES="" SSO_TOOLS=""
  GRAFANA="" BACKUP="" DOCKERHUB_USER="" DOCKERHUB_TOKEN="" INSTALL_USER=""
  local pairs key value
  pairs=$(mktemp)
  if ! python3 "$KIT_DIR/scripts/autoinstall-settings.py" "$CONF" --server > "$pairs"; then
    rm -f "$pairs"
    fail "a value in $CONF is not valid (see above)" "correct it, then: sudo $KIT_DIR/scripts/autoinstall.sh start"
  fi
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    printf -v "$key" '%s' "$value"
  done < "$pairs"
  rm -f "$pairs"
}

first_address() { python3 -c 'import ipaddress, sys; print(f"{ipaddress.ip_network(sys.argv[1]).network_address + 1}/32")' "$1"; }

as_user() {  # as_user COMMAND...: in the kit folder, as the platform's account, with a fresh log-in environment
  local home
  home=$(getent passwd "$INSTALL_USER" | cut -d: -f6)
  # shellcheck disable=SC2016  # $0 and $@ belong to the inner bash
  runuser -u "$INSTALL_USER" -- env -i HOME="$home" USER="$INSTALL_USER" LOGNAME="$INSTALL_USER" SHELL=/bin/bash \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    bash -c 'set -a; if [ -r /etc/environment ]; then . /etc/environment; fi; set +a; cd "$0" && exec "$@"' "$KIT_DIR" "$@"
}
installer() {  # installer ARGS...: ./scripts/install.sh as the platform's account; stop the install when it stops
  as_user ./scripts/install.sh "$@" || fail "the installer stopped (its reason and fix are above)" \
    "do what the Fix line above says (as $INSTALL_USER: sudo -iu $INSTALL_USER, then cd $KIT_DIR)"
}

# ------------------------------------------------------------------ steps
TIMERS_STOPPED=0
apt_free() {  # true when no apt or dpkg holds its lock
  python3 - <<'PY'
import fcntl, os, sys
for path in ("/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock", "/var/lib/apt/lists/lock"):
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        continue
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.lockf(fd, fcntl.LOCK_UN)
    except OSError:
        sys.exit(1)
    finally:
        os.close(fd)
PY
}
restore_timers() {
  if [ "$TIMERS_STOPPED" = 1 ]; then systemctl start apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true; fi
}
step_apt() {
  # A cloud server runs Ubuntu's daily update right after its first start, and apt serves one at a time.
  printf 'DPkg::Lock::Timeout "600";\n' > /etc/apt/apt.conf.d/80ioh-msp-wait-for-lock
  if systemctl stop apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1; then TIMERS_STOPPED=1; fi
  local waited=0
  until apt_free; do
    [ "$waited" = 0 ] && info "waiting for Ubuntu's own update to finish..."
    sleep 10
    waited=$((waited + 10))
    [ "$waited" -lt 1800 ] || fail "apt stayed busy for 30 minutes" \
      "ps aux | grep -E 'apt|dpkg' shows what holds it; once it has finished: sudo $KIT_DIR/scripts/autoinstall.sh start"
  done
  if [ "$waited" -gt 0 ]; then ok "apt is free (waited ${waited}s)"; else ok "apt is free"; fi
}
step_account() {
  if id "$INSTALL_USER" >/dev/null 2>&1; then
    ok "account $INSTALL_USER exists"
  else
    useradd --create-home --shell /bin/bash --comment "IOH MSP platform" "$INSTALL_USER"
    ok "account $INSTALL_USER created, without a password: sudo -iu $INSTALL_USER from any account with sudo"
  fi
  # The kit's backup and preparation scripts use sudo. Docker rights already equal root's, so this adds no power.
  printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$INSTALL_USER" > /etc/sudoers.d/90-ioh-msp
  chmod 440 /etc/sudoers.d/90-ioh-msp
  if ! visudo -cqf /etc/sudoers.d/90-ioh-msp; then
    rm -f /etc/sudoers.d/90-ioh-msp
    fail "the sudo rule for $INSTALL_USER was not valid, so it was removed" "check INSTALL_USER in $CONF"
  fi
  ok "$INSTALL_USER may use sudo, for the kit's backup and preparation scripts"
}
step_kit() {
  [ -f "$KIT_DIR/scripts/install.sh" ] || fail "$KIT_DIR is not the kit folder" "run the copy of autoinstall.sh inside the kit"
  chmod +x "$KIT_DIR"/scripts/*.sh "$KIT_DIR"/environments/*/*.sh 2>/dev/null || true
  if [ "$(stat -c %U "$KIT_DIR")" != "$INSTALL_USER" ]; then
    chown -R "$INSTALL_USER": "$KIT_DIR"
    ok "the kit $KIT_DIR (version $VERSION) now belongs to $INSTALL_USER"
  else
    ok "the kit $KIT_DIR (version $VERSION) belongs to $INSTALL_USER"
  fi
}
step_prepare() {
  local args=(--pool "$POOL" --tz "$TIMEZONE")
  # Ubuntu's updates once; a run after a fix only checks (an upgraded Docker would restart every container).
  if step_done prepare; then args+=(--no-upgrade); fi
  case "$ENVIRONMENT" in
    gcp | aws) args+=(--no-firewall) ;;   # the cloud's firewall in front of the server lets in only SSH
    vps) args+=(--admin-networks "$SSH_FROM") ;;
    ioh)
      args+=(--admin-networks "$ADMIN_NETWORKS")
      if [ -n "$PROXY" ]; then args+=(--proxy "$PROXY"); fi
      if [ -n "$PROXY_CA" ]; then args+=(--proxy-ca "$PROXY_CA"); fi
      ;;
  esac
  SUDO_USER=$INSTALL_USER bash "$KIT_DIR/scripts/prepare-server.sh" "${args[@]}" ||
    fail "the server preparation stopped (its reason and fix are above)" "do what the Fix line above says"
}
step_settings() {
  local admin listen
  if [ "$ENVIRONMENT" = ioh ]; then
    admin=$ADMIN_NETWORKS listen=0.0.0.0
    # With a tunnel on this server as well, its visitors arrive from the platform network's first address.
    if [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ]; then admin="$admin $(first_address "$SUBNET")"; fi
  else
    # Outside IOH the Cloudflare Tunnel is the only way in: ports 80, 443 and 10051 stay on this server.
    admin=$(first_address "$SUBNET") listen=127.0.0.1
  fi
  local args=(settings --domain "$DOMAIN" --subnet "$SUBNET" --admin-networks "$admin"
              --web-listen "$listen" --collectors-listen "$listen" --tz "$TIMEZONE")
  if [ -n "$NOC_EMAIL" ]; then args+=(--email "$NOC_EMAIL"); fi
  installer "${args[@]}"
}
step_dockerhub() {
  printf '%s' "$DOCKERHUB_TOKEN" | as_user docker login --username "$DOCKERHUB_USER" --password-stdin >/dev/null 2>&1 ||
    fail "Docker Hub refused the log-in" "check DOCKERHUB_USER and DOCKERHUB_TOKEN (a personal access token) in $CONF"
  ok "logged in to Docker Hub as $DOCKERHUB_USER"
}
step_core() { installer; }
step_proxy() { installer proxy; }
step_grafana() { installer grafana; }
step_console() { installer console; }
step_ssotools() { installer sso-tools; }
step_samples() { installer samples; }
step_sso() {
  # The one-time password stays out of the log: it goes to the summary, which only root can read.
  local out pw
  out=$(mktemp)
  chmod 600 "$out"
  if ! as_user ./scripts/install.sh sso --admin-email "$STAFF_EMAIL" --name "$STAFF_NAME" > "$out" 2>&1; then
    sed -E 's|(one-time password: )[^ ]+|\1(hidden)|' "$out"
    rm -f "$out"
    fail "the installer stopped (its reason and fix are above)" "do what the Fix line above says"
  fi
  sed -E 's|(one-time password: )[^ ]+|\1(see: sudo ./scripts/autoinstall.sh status)|' "$out"
  pw=$(grep -oE 'one-time password: [^ ]+' "$out" | head -n 1 | cut -d' ' -f3 || true)
  rm -f "$out"
  if [ -n "$pw" ]; then
    (umask 077; printf '%s\n' "$pw" > "$PASSWORD_FILE")
  fi
}
step_backup() {
  printf '15 0 * * * root %s/scripts/backup.sh >> /var/log/msp-backup.log 2>&1\n' "$KIT_DIR" > /etc/cron.d/msp-backup
  chmod 644 /etc/cron.d/msp-backup
  ok "nightly backup at 00:15 to /var/backups/msp, the last 14 kept (/etc/cron.d/msp-backup)"
}

# ------------------------------------------------------------------ Cloudflare Tunnel
install_cloudflared() {
  if command -v cloudflared >/dev/null; then
    ok "cloudflared is installed ($(cloudflared --version 2>/dev/null | awk '{print $3}'))"
    return
  fi
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL --retry 3 -o /usr/share/keyrings/cloudflare-main.gpg https://pkg.cloudflare.com/cloudflare-main.gpg ||
    fail "Cloudflare's package key could not be downloaded" "the server needs HTTPS to pkg.cloudflare.com"
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    > /etc/apt/sources.list.d/cloudflared.list
  if ! { apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloudflared >/dev/null; }; then
    fail "cloudflared could not be installed from Cloudflare's packages" "sudo apt-get install cloudflared shows why"
  fi
  ok "cloudflared installed from Cloudflare's packages ($(cloudflared --version 2>/dev/null | awk '{print $3}'))"
}
connect_tunnel() {  # connect_tunnel TOKEN [again]: run cloudflared as a service with this tunnel's token
  local token=$1 again=${2:-} out line waited=0
  install_cloudflared
  if [ -z "$again" ] && systemctl is-active --quiet cloudflared; then
    ok "the tunnel's connector runs already (sudo ./scripts/autoinstall.sh tunnel connects another token)"
    return
  fi
  if [ -f /etc/systemd/system/cloudflared.service ]; then cloudflared service uninstall >/dev/null 2>&1 || true; fi
  out=$(mktemp)
  if ! cloudflared service install "$token" > "$out" 2>&1; then
    while IFS= read -r line; do printf '%s\n' "${line//"$token"/(token)}"; done < "$out" | tail -n 5
    rm -f "$out"
    fail "cloudflared did not accept the token" \
      "copy the token again (Cloudflare dashboard: Networking, Tunnels, your tunnel), then: sudo $KIT_DIR/scripts/autoinstall.sh tunnel"
  fi
  rm -f "$out"
  until systemctl is-active --quiet cloudflared; do
    sleep 2
    waited=$((waited + 2))
    [ "$waited" -lt 60 ] || fail "the cloudflared service does not start" "sudo journalctl -u cloudflared --no-pager | tail -n 20 shows why"
  done
  waited=0
  until journalctl -u cloudflared --since "-5min" --no-pager 2>/dev/null | grep -q "Registered tunnel connection"; do
    sleep 3
    waited=$((waited + 3))
    if [ "$waited" -ge 60 ]; then
      warn "the connector runs, but Cloudflare has not confirmed a connection yet" \
        "sudo journalctl -u cloudflared --no-pager | tail -n 20 shows why; the server needs outbound port 7844"
      return
    fi
  done
  ok "the tunnel is connected: Cloudflare sends this domain's names to this server"
}

# ------------------------------------------------------------------ summary
write_summary() {
  local pw tunnel samples
  pw=$(cat "$PASSWORD_FILE" 2>/dev/null || true)
  if systemctl is-active --quiet cloudflared 2>/dev/null; then tunnel="connected (the cloudflared service runs)"
  elif [ "$ENVIRONMENT" = ioh ]; then tunnel="none: browsers reach the server through IOH's network and DNS"
  else tunnel="not connected yet: sudo $KIT_DIR/scripts/autoinstall.sh tunnel (asks for the token)"; fi
  if [ "$SAMPLES" = yes ]; then samples="on: PT ACME Retail Indonesia and PT Bank Contoh Tbk (off: ./scripts/install.sh samples off)"
  else samples="off (on: ./scripts/install.sh samples)"; fi
  (
    umask 077
    {
      printf 'IOH MSP platform, installed %s by the automatic install (kit %s, %s)\n\n' "$(date '+%Y-%m-%d %H:%M %Z')" "$VERSION" "$ENVIRONMENT"
      printf '  MSP Console   https://console.%s\n' "$DOMAIN"
      if [ -n "$pw" ]; then
        printf '  First sign-in %s with the one-time password %s\n' "$STAFF_EMAIL" "$pw"
        printf '                (Keycloak then asks for your own password; add an authenticator app after)\n'
      else
        printf '  First sign-in %s (account made earlier; a new one-time password:\n' "$STAFF_EMAIL"
        printf '                sudo -iu %s, then cd %s && docker compose exec msp-glue mspctl staff reset %s)\n' "$INSTALL_USER" "$KIT_DIR" "$STAFF_EMAIL"
      fi
      printf '  Tools         https://nautobot.%s  https://zabbix.%s  https://glpi.%s\n' "$DOMAIN" "$DOMAIN" "$DOMAIN"
      printf '                https://semaphore.%s  https://git.%s  https://glue.%s/healthz\n' "$DOMAIN" "$DOMAIN" "$DOMAIN"
      printf '  Passwords     every administrator password is in %s/.env: copy that file into the\n' "$KIT_DIR"
      printf '                password manager now (sudo cat %s/.env, then clear the screen)\n' "$KIT_DIR"
      printf '  Tunnel        %s\n' "$tunnel"
      printf '  Sample data   %s\n' "$samples"
      if [ "$BACKUP" = yes ]; then printf '  Backups       every night at 00:15 to /var/backups/msp\n'; fi
      printf '  Work on it    sudo -iu %s, then cd %s (the kit and its commands belong to that account)\n' "$INSTALL_USER" "$KIT_DIR"
    } > "$SUMMARY"
  )
}

# ------------------------------------------------------------------ commands
cmd_run() {
  mkdir -p "$VAR_DIR"
  chmod 755 "$VAR_DIR"
  if [ ! -f "$LOG" ]; then install -m 600 /dev/null "$LOG"; fi
  # The service writes straight into the log; in a terminal, show the output and keep a copy.
  if [ "${IOH_MSP_SERVICE:-}" != 1 ]; then exec > >(tee -a "$LOG") 2>&1; fi
  exec 9> "$VAR_DIR/autoinstall.lock"
  flock -n 9 || fail "the automatic install runs already" "sudo $KIT_DIR/scripts/autoinstall.sh status shows how far it is"
  CURRENT="0 of ${#STEPS[@]}: the settings file $CONF"
  load_conf
  trap restore_timers EXIT
  step "IOH MSP platform: automatic install (kit $VERSION, $ENVIRONMENT, $DOMAIN), $(date '+%Y-%m-%d %H:%M %Z')"
  local i=0 total=${#STEPS[@]} s
  for s in "${STEPS[@]}"; do
    i=$((i + 1))
    CURRENT="$i of $total: $(title_of "$s")"
    if ! enabled "$s"; then continue; fi
    if [[ " $ALWAYS " != *" $s "* ]] && step_done "$s"; then continue; fi
    set_status running "$CURRENT" "running"
    step "Step $CURRENT"
    "step_$s"
    mark_done "$s"
  done
  CURRENT=""
  write_summary
  touch "$DONE"
  set_status finished "$total of $total" "installed"
  step "Finished"
  info "The platform is installed. The first sign-in, the addresses and the next steps:"
  info "  sudo $KIT_DIR/scripts/autoinstall.sh status"
}

write_service() {
  cat > "/etc/systemd/system/$UNIT.service" <<EOF
# Made by $KIT_DIR/scripts/autoinstall.sh: the platform's automatic install. It starts at every
# boot until the install has finished, so a restart in the middle continues where it stopped. It runs
# in the background (Type=exec), so the server's start never waits for it.
[Unit]
Description=IOH MSP platform: automatic install
Wants=network-online.target
After=network-online.target
ConditionPathExists=!$DONE

[Service]
Type=exec
Environment=IOH_MSP_SERVICE=1
ExecStart=/bin/bash $KIT_DIR/scripts/autoinstall.sh run --config $CONF
StandardOutput=append:$LOG
StandardError=append:$LOG

[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/update-motd.d/90-ioh-msp <<EOF
#!/bin/sh
# Made by $KIT_DIR/scripts/autoinstall.sh: the automatic install's progress at every log-in.
f=$STATUS
[ -r "\$f" ] || exit 0
state=\$(sed -n 's/^state=//p' "\$f"); step=\$(sed -n 's/^step=//p' "\$f"); since=\$(sed -n 's/^since=//p' "\$f")
echo
case "\$state" in
  running) echo "IOH MSP platform: the automatic install is running, step \$step (since \$since)." ;;
  stopped) echo "IOH MSP platform: the automatic install STOPPED at step \$step." ;;
  finished) echo "IOH MSP platform: installed (\$since)." ;;
esac
echo "  How far, or the first sign-in: sudo $KIT_DIR/scripts/autoinstall.sh status"
EOF
  chmod 755 /etc/update-motd.d/90-ioh-msp
}

cmd_start() {
  load_conf   # mistakes show here, before anything runs in the background
  if [ -f "$DONE" ]; then
    ok "the platform is installed already"
    info "The first sign-in and the addresses: sudo $KIT_DIR/scripts/autoinstall.sh status"
    return
  fi
  if [ ! -d /run/systemd/system ]; then
    info "no systemd on this machine: the install runs in this terminal"
    cmd_run
    return
  fi
  mkdir -p "$VAR_DIR"
  chmod 755 "$VAR_DIR"
  if [ ! -f "$LOG" ]; then install -m 600 /dev/null "$LOG"; fi
  write_service
  systemctl daemon-reload
  systemctl enable "$UNIT" >/dev/null 2>&1
  systemctl reset-failed "$UNIT" >/dev/null 2>&1 || true
  if [ "$(status_value state)" != running ]; then set_status running "starting" "starting"; fi
  systemctl start --no-block "$UNIT"
  ok "the automatic install runs in the background (service $UNIT): 40 to 75 minutes in all"
  info "How far it is:   sudo $KIT_DIR/scripts/autoinstall.sh status"
  info "Follow it live:  sudo tail -f $LOG"
}

install_running() {  # true while a run holds the lock, or the service is starting
  if [ -f "$VAR_DIR/autoinstall.lock" ] && ! flock -n "$VAR_DIR/autoinstall.lock" true 2>/dev/null; then return 0; fi
  case "$(systemctl show -p ActiveState --value "$UNIT" 2>/dev/null)" in activating | active) return 0 ;; esac
  return 1
}
cmd_status() {
  local state
  state=$(status_value state)
  step "IOH MSP platform: automatic install (kit $VERSION)"
  case "$state" in
    "")
      info "It has not started on this server. Start it: sudo $KIT_DIR/scripts/autoinstall.sh start" ;;
    running)
      if install_running; then
        printf '  ....   running: step %s, since %s\n' "$(status_value step)" "$(status_value since)"
        info "Follow it live: sudo tail -f $LOG"
      else
        printf '  ....   interrupted at step %s (the server restarted?)\n' "$(status_value step)"
        info "It continues at the next start of the server, or now: sudo $KIT_DIR/scripts/autoinstall.sh start"
      fi
      if [ -r "$LOG" ]; then printf '\n  Last lines of %s:\n' "$LOG"; tail -n 8 "$LOG" | sed 's/^/    /'; fi ;;
    stopped)
      printf '  %sSTOPPED%s at step %s (%s)\n' "$R" "$N" "$(status_value step)" "$(status_value since)"
      if [ -r "$LOG" ]; then printf '\n  The end of %s, with the reason and the fix:\n' "$LOG"; tail -n 25 "$LOG" | sed 's/^/    /'; fi
      info ""
      info "After the fix: sudo $KIT_DIR/scripts/autoinstall.sh start (continues with that step)" ;;
    finished)
      if [ -r "$SUMMARY" ]; then printf '\n'; cat "$SUMMARY"
      else ok "installed $(status_value since)"; fi ;;
  esac
}

cmd_tunnel() {
  local token=""
  if [ -t 0 ]; then
    printf 'Cloudflare tunnel token (the long string after "cloudflared service install"; input hidden): '
    IFS= read -rs token
    echo
  else
    IFS= read -r token || true
  fi
  # The last word: someone may paste the whole "sudo cloudflared service install <token>" line, or a space after.
  local words=()
  read -r -a words <<< "$token" || true
  if [ ${#words[@]} -gt 0 ]; then token=${words[${#words[@]}-1]}; else token=""; fi
  [[ "$token" =~ ^[A-Za-z0-9+/=_-]{50,}$ ]] || fail "that is not a tunnel token" \
    "Cloudflare dashboard: Networking, Tunnels, your tunnel; copy the long string after \"cloudflared service install\""
  step "Cloudflare Tunnel"
  connect_tunnel "$token" again
  if [ -r "$CONF" ] && [ -f "$DONE" ]; then load_conf; write_summary; fi
}

CMD=start
if [ $# -gt 0 ] && [[ "$1" != -* ]]; then CMD=$1; shift; fi
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONF=${2:-}; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) fail "unknown option '$1'" "./scripts/autoinstall.sh --help lists the commands" ;;
  esac
done
case "$CMD" in
  -h | --help | help) usage; exit 0 ;;
esac
[ "$(id -u)" = 0 ] || fail "the automatic install runs as root" "sudo $KIT_DIR/scripts/autoinstall.sh $CMD"
case "$CMD" in
  start) cmd_start ;;
  run) cmd_run ;;
  status) cmd_status ;;
  tunnel) cmd_tunnel ;;
  *) fail "unknown command '$CMD'" "./scripts/autoinstall.sh --help lists the commands" ;;
esac
