#!/usr/bin/env bash
# =============================================================================
# Remove what environments/gcp/create.sh made: the virtual machine with its disk, its firewall rules,
# and its network when nothing else uses it. Everything on the server is lost; back up first if it
# matters (sudo ./scripts/backup.sh, then copy /var/backups/msp elsewhere). The machine's fixed address
# (NAME-ip, direct access) stays for a rebuild, so its DNS record stays right; --release-address
# releases it too.
#
#   ./environments/gcp/delete.sh --project <project ID>
#
# Options:
#   --project ID         the Google Cloud project
#   --name NAME          the virtual machine (default msp-test-01)
#   --zone ZONE          default asia-southeast2-a
#   --release-address    release the address NAME-ip as well (when the trial is over)
#   --yes                do not ask to type the name first
# =============================================================================
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT="" NAME=msp-test-01 ZONE=asia-southeast2-a YES=0 RELEASE=0
NETWORK=msp-net

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; R=; N=; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %sOK%s     %s\n' "$G" "$N" "$*"; }
info() { printf '         %s\n' "$*"; }
fail() { printf '\n  %sSTOPPED%s %s\n' "$R" "$N" "$1"; [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2"; exit 1; }
usage() { sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT=${2:-}; shift 2 ;;
    --name) NAME=${2:-}; shift 2 ;;
    --zone) ZONE=${2:-}; shift 2 ;;
    --release-address) RELEASE=1; shift ;;
    --yes) YES=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "unknown option '$1'" "./environments/gcp/delete.sh --help lists the options" ;;
  esac
done
[ -n "$PROJECT" ] || fail "--project is missing" "./environments/gcp/delete.sh --project <project ID>"
command -v gcloud >/dev/null || fail "gcloud is not installed here" "use Cloud Shell, or install the Google Cloud CLI"
REGION=${ZONE%-*}
SUBNET="$NETWORK-$REGION"

if [ "$YES" = 0 ]; then
  printf 'This deletes the virtual machine %s in %s, with its disk and everything on it.\n' "$NAME" "$PROJECT"
  printf 'Type its name to go on: '
  read -r answer
  [ "$answer" = "$NAME" ] || fail "the name did not match, so nothing was deleted" "run the command again and type $NAME"
fi

step "Virtual machine $NAME"
if gcloud compute instances describe "$NAME" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud compute instances delete "$NAME" --zone "$ZONE" --project "$PROJECT" --delete-disks=all --quiet
  ok "virtual machine $NAME and its disk deleted"
else
  ok "no virtual machine $NAME in $ZONE"
fi
# The fixed address (direct access, or collectors in the README's day-to-day notes): kept for a rebuild,
# so the DNS record stays right; it costs a little while no machine uses it.
if gcloud compute addresses describe "$NAME-ip" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  kept=$(gcloud compute addresses describe "$NAME-ip" --region "$REGION" --project "$PROJECT" --format='value(address)')
  if [ "$RELEASE" = 1 ]; then
    gcloud compute addresses delete "$NAME-ip" --region "$REGION" --project "$PROJECT" --quiet
    ok "external address $NAME-ip ($kept) released: delete its DNS records too"
  else
    ok "external address $NAME-ip ($kept) kept for a rebuild (about US\$7 a month while unused)"
    info "When the trial is over: the same command with --release-address releases it."
  fi
fi

step "Firewall rules and network"
for r in "$NETWORK-ssh-iap" "$NETWORK-ssh-direct" "$NETWORK-collectors" "$NETWORK-web"; do
  if gcloud compute firewall-rules describe "$r" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud compute firewall-rules delete "$r" --project "$PROJECT" --quiet
    ok "firewall rule $r deleted"
  fi
done
others=$(gcloud compute instances list --project "$PROJECT" --filter="networkInterfaces.network:$NETWORK" --format='value(name)' 2>/dev/null || true)
if [ -n "$others" ]; then
  info "network $NETWORK kept: these virtual machines still use it: $(printf '%s' "$others" | tr '\n' ' ')"
  exit 0
fi
if gcloud compute networks subnets describe "$SUBNET" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud compute networks subnets delete "$SUBNET" --region "$REGION" --project "$PROJECT" --quiet
  ok "subnet $SUBNET deleted"
fi
if gcloud compute networks describe "$NETWORK" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud compute networks delete "$NETWORK" --project "$PROJECT" --quiet
  ok "network $NETWORK deleted"
fi
info "With a Cloudflare Tunnel, the tunnel, its routes and Cloudflare Access stay in Cloudflare: a rebuild"
info "(create.sh again) uses them as they are. To remove them too: Networking, Tunnels, and Zero Trust, Access."
info "With direct access, the DNS records stay at your DNS provider, pointing at the kept address."
