#!/usr/bin/env bash
# =============================================================================
# Make the start script of an automatic install from a settings file: one self-contained script
# that, run as root on an empty Ubuntu 24.04 server, downloads and checks the kit, then starts
# scripts/autoinstall.sh. The Google Cloud and AWS create scripts use it for --autoinstall; for a
# VPS, give its result to the provider as user data, or run it on the server as root.
#
#   ./environments/bootstrap.sh my.conf ENVIRONMENT=vps > msp-autoinstall.sh
#   ./environments/bootstrap.sh --check my.conf          only check the settings file
#
# KEY=VALUE after the file sets or replaces a value. The kit the server downloads: KIT_URL and
# KIT_SHA256 from the file, or else this kit's own version from GitHub, with its checksum from the
# dist/SHA256SUMS next to it. The result holds your settings: keep it as private as they are.
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
KIT_VERSION=$(cat "$HERE/../VERSION" 2>/dev/null || echo "")
REPO_DIST=https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist

fail() { printf '  STOPPED %s\n' "$1" >&2; [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2" >&2; exit 1; }
usage() { sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'; }

CHECK=0
case "${1:-}" in
  -h | --help | "") usage; exit 0 ;;
  --check) CHECK=1; shift ;;
esac
CONF=${1:-}
[ -n "$CONF" ] || fail "the settings file is missing" "./environments/bootstrap.sh my.conf (copy environments/autoinstall.conf.example)"
[ -r "$CONF" ] || fail "cannot read $CONF" "give the path of your settings file"
shift

# The settings, read and checked as the server will (scripts/autoinstall-settings.py), as clean lines.
SETTINGS="$HERE/../scripts/autoinstall-settings.py"
work=$(mktemp)
trap 'rm -f "$work"' EXIT
python3 "$SETTINGS" "$CONF" --clean "$@" > "$work" ||
  fail "the settings file $CONF is not ready (see above)" \
    "correct it (environments/autoinstall.conf.example explains every value), then the same command again"
[ "$CHECK" = 0 ] || exit 0
KIT_URL="" KIT_SHA256=""
while IFS= read -r -d '' key && IFS= read -r -d '' value; do
  case "$key" in KIT_URL) KIT_URL=$value ;; KIT_SHA256) KIT_SHA256=$value ;; esac
done < <(python3 "$SETTINGS" "$work")

# The kit to download, and its checksum.
if [ -z "$KIT_URL" ]; then
  [ -n "$KIT_VERSION" ] || fail "this kit has no VERSION file, so the download is unknown" "set KIT_URL and KIT_SHA256 in $CONF"
  KIT_URL="$REPO_DIST/ioh-msp-stack-v$KIT_VERSION.zip"
fi
if [ -z "$KIT_SHA256" ]; then
  sums=$(curl -fsSL --retry 3 "${KIT_URL%/*}/SHA256SUMS" 2>/dev/null) ||
    fail "could not read ${KIT_URL%/*}/SHA256SUMS for the kit's checksum" \
      "check the link (a new version is there once its pull request is merged), or set KIT_SHA256 in $CONF"
  KIT_SHA256=$(printf '%s\n' "$sums" | awk -v f="${KIT_URL##*/}" '$2 == f {print $1}')
  [[ "$KIT_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "SHA256SUMS has no line for ${KIT_URL##*/}" "check KIT_URL, or set KIT_SHA256 in $CONF"
fi

cat <<EOF
#!/bin/bash
# IOH MSP platform: start of the automatic install, made by environments/bootstrap.sh (kit ${KIT_VERSION:-?})
# on $(date '+%Y-%m-%d'). Runs as root on an empty Ubuntu 24.04 server: as cloud-init user data, as a
# Google Cloud startup script, or pasted into a root shell. Holds your settings: keep it private.
set -euo pipefail
umask 022   # the kit's files must stay readable for the containers that mount them
KIT_URL='$KIT_URL'
KIT_SHA256='$KIT_SHA256'
mkdir -p /etc/ioh-msp
chmod 700 /etc/ioh-msp
(umask 077; cat > /etc/ioh-msp/autoinstall.conf) <<'IOH_MSP_SETTINGS'
$(cat "$work")
IOH_MSP_SETTINGS
if [ ! -f /opt/ioh-msp-stack/scripts/autoinstall.sh ]; then
  echo "IOH MSP: downloading the kit from \$KIT_URL"
  curl -fsSL --retry 5 --retry-delay 5 -o /var/tmp/ioh-msp-stack.zip "\$KIT_URL"
  echo "\$KIT_SHA256  /var/tmp/ioh-msp-stack.zip" | sha256sum -c --quiet -
  python3 - /var/tmp/ioh-msp-stack.zip /opt <<'PY'
import os, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:  # like unzip: scripts keep their execute bit
    for item in z.infolist():
        path = z.extract(item, sys.argv[2])
        if item.external_attr >> 16 & 0o111 and not item.is_dir():
            os.chmod(path, 0o755)
PY
  rm -f /var/tmp/ioh-msp-stack.zip
fi
bash /opt/ioh-msp-stack/scripts/autoinstall.sh start
EOF
