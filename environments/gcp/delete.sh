#!/usr/bin/env bash
# =============================================================================
# Remove what environments/gcp/create.sh made: the virtual machine with its disk, the address kept for
# it (NAME-ip), its firewall rules, and its network when nothing else uses it. Everything on the server
# is lost; back up first if it matters (sudo ./scripts/backup.sh, then copy /var/backups/msp elsewhere).
#
#   ./environments/gcp/delete.sh --project <project ID>
#
# Options:
#   --project ID   the Google Cloud project
#   --name NAME    the virtual machine (default msp-test-01)
#   --zone ZONE    default asia-southeast2-a
#   --yes          do not ask to type the name first
# =============================================================================
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT="" NAME=msp-test-01 ZONE=asia-southeast2-a YES=0
NETWORK=msp-net

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; R=; N=; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %sOK%s     %s\n' "$G" "$N" "$*"; }
info() { printf '         %s\n' "$*"; }
fail() { printf '\n  %sSTOPPED%s %s\n' "$R" "$N" "$1"; [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2"; exit 1; }
usage() { sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT=${2:-}; shift 2 ;;
    --name) NAME=${2:-}; shift 2 ;;
    --zone) ZONE=${2:-}; shift 2 ;;
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
# The external address kept for collectors (the README's day-to-day notes) costs money while unused.
if gcloud compute addresses describe "$NAME-ip" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud compute addresses delete "$NAME-ip" --region "$REGION" --project "$PROJECT" --quiet
  ok "external address $NAME-ip released"
fi

step "Firewall rules and network"
for r in "$NETWORK-ssh-iap" "$NETWORK-ssh-direct" "$NETWORK-collectors"; do
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
info "The Cloudflare tunnel, its routes and Cloudflare Access stay in Cloudflare: a rebuild (create.sh"
info "again) uses them as they are. To remove them too: Networking, Tunnels, and Zero Trust, Access."
