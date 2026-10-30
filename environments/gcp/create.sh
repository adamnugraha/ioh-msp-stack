#!/usr/bin/env bash
# =============================================================================
# Create the platform's server on Google Cloud: one Ubuntu 24.04 virtual machine in a network of its
# own, where only SSH through Google's Identity-Aware Proxy (IAP) gets in. Run it in Cloud Shell
# (console.cloud.google.com, the >_ button), where gcloud is ready, or on a PC with the gcloud CLI.
#
#   ./environments/gcp/create.sh --project <project ID>
#
# Options:
#   --project ID          the Google Cloud project, with billing (a Free Trial account works)
#   --name NAME           the virtual machine (default msp-test-01)
#   --zone ZONE           default asia-southeast2-a (Jakarta)
#   --machine TYPE        default e2-standard-4 (4 vCPU, 16 GB); e2-standard-2 also works, more slowly
#   --disk GB             boot disk (default 100, at least 40)
#   --range CIDR          the network's subnet (default 10.200.0.0/24)
#   --ssh-from "CIDR ..." also allow plain SSH from these addresses (an SSH client outside Google
#                         Cloud, such as Termix or PuTTY); needs --public-key
#   --public-key FILE     your SSH public key, for that client
#   --user NAME           the Linux account for that key (default: your user name here)
#   --collectors-from "CIDR ..."  open port 10051 to collectors at these addresses
#   --dry-run             print the gcloud commands without running them
#
# Running it again is safe: what exists is kept, and the firewall rules follow the options given.
# ./environments/gcp/delete.sh removes everything this script created.
# =============================================================================
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT="" NAME=msp-test-01 ZONE=asia-southeast2-a MACHINE=e2-standard-4 DISK=100 RANGE=10.200.0.0/24
SSH_FROM="" PUBKEY="" LINUX_USER=${USER:-$(id -un)} COLLECTORS="" DRY=0
NETWORK=msp-net TAG=msp-platform IAP_RANGE=35.235.240.0/20

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; R=; N=; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %sOK%s     %s\n' "$G" "$N" "$*"; }
info() { printf '         %s\n' "$*"; }
fail() { printf '\n  %sSTOPPED%s %s\n' "$R" "$N" "$1"; [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2"; exit 1; }
usage() { sed -n '3,24p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT=${2:-}; shift 2 ;;
    --name) NAME=${2:-}; shift 2 ;;
    --zone) ZONE=${2:-}; shift 2 ;;
    --machine) MACHINE=${2:-}; shift 2 ;;
    --disk) DISK=${2:-}; shift 2 ;;
    --range) RANGE=${2:-}; shift 2 ;;
    --ssh-from) SSH_FROM=${2:-}; shift 2 ;;
    --public-key) PUBKEY=${2:-}; shift 2 ;;
    --user) LINUX_USER=${2:-}; shift 2 ;;
    --collectors-from) COLLECTORS=${2:-}; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "unknown option '$1'" "./environments/gcp/create.sh --help lists the options" ;;
  esac
done

# ------------------------------------------------------------------ check the options
[ -n "$PROJECT" ] || fail "--project is missing" "./environments/gcp/create.sh --project <project ID> (gcloud projects list shows yours)"
python3 - "$PROJECT" "$NAME" "$ZONE" "$DISK" "$RANGE" "$SSH_FROM" "$COLLECTORS" "$LINUX_USER" <<'PY' || fail "an option is not valid (see above)" "correct it and run the command again"
import ipaddress, re, sys
project, name, zone, disk, rng, ssh_from, collectors, user = sys.argv[1:9]
errors = []
if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
    errors.append(f"--project '{project}' is not a project ID such as ioh-msp-test (the ID, not the display name)")
if not re.fullmatch(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?", name):
    errors.append(f"--name '{name}': lowercase letters, digits and dashes, starting with a letter")
if not re.fullmatch(r"[a-z]+-[a-z]+[0-9]-[a-z]", zone):
    errors.append(f"--zone '{zone}' is not a zone such as asia-southeast2-a")
if not disk.isdigit() or int(disk) < 40:
    errors.append(f"--disk '{disk}': a number of GB, 40 or more")
try:
    net = ipaddress.ip_network(rng, strict=True)
    for other, what in (("172.30.0.0/24", "the platform network"), ("172.31.0.0/16", "Docker's address pool")):
        if net.overlaps(ipaddress.ip_network(other)):
            errors.append(f"--range {rng} overlaps {other}, {what} on the server")
except ValueError:
    errors.append(f"--range '{rng}' is not a network such as 10.200.0.0/24")
for flag, value in (("--ssh-from", ssh_from), ("--collectors-from", collectors)):
    for item in value.split():
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError:
            errors.append(f"{flag}: '{item}' is not an address range such as 203.0.113.7/32")
if ssh_from and not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
    errors.append(f"--user '{user}' is not a Linux user name such as adam")
for e in errors:
    print("  FAIL   " + e)
sys.exit(1 if errors else 0)
PY
if [ -n "$SSH_FROM" ]; then
  [ -n "$PUBKEY" ] || fail "--ssh-from needs --public-key, the key your SSH client logs in with" "add --public-key ~/.ssh/id_ed25519.pub (upload the file to Cloud Shell first: the three-dot menu, Upload)"
  [ -r "$PUBKEY" ] && grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp[0-9]+) ' "$PUBKEY" ||
    fail "$PUBKEY is not an SSH public key" "give the .pub file, the one that starts with ssh-ed25519 or ssh-rsa"
fi
REGION=${ZONE%-*}
SUBNET="$NETWORK-$REGION"

run() {  # run ARGS...: gcloud ARGS, or only show it in a dry run
  if [ "$DRY" = 1 ]; then printf '  would run: gcloud'; printf ' %q' "$@"; printf '\n'; return 0; fi
  gcloud "$@"
}
exists() {  # exists ARGS...: true when "gcloud ARGS" (a describe) finds the resource; never in a dry run
  [ "$DRY" = 1 ] && return 1
  gcloud "$@" >/dev/null 2>&1
}
rule() {  # rule NAME PORTS "CIDR ..." WHAT: a firewall rule that lets exactly these sources in
  local ranges; ranges=$(printf '%s' "$3" | tr -s ' ' ',')
  if exists compute firewall-rules describe "$1" --project "$PROJECT"; then
    run compute firewall-rules update "$1" --project "$PROJECT" --source-ranges="$ranges" --quiet
    ok "firewall rule $1: $2 from $3 ($4)"
  else
    run compute firewall-rules create "$1" --project "$PROJECT" --network "$NETWORK" --direction=INGRESS \
      --action=allow --rules="$2" --source-ranges="$ranges" --target-tags="$TAG" --quiet
    ok "firewall rule $1 created: $2 from $3 ($4)"
  fi
}
drop_rule() {  # drop_rule NAME: remove a rule an earlier run made, when its option is gone now
  if exists compute firewall-rules describe "$1" --project "$PROJECT"; then
    run compute firewall-rules delete "$1" --project "$PROJECT" --quiet
    ok "firewall rule $1 removed (its option was not given this time)"
  fi
}

[ "$DRY" = 1 ] && printf '%sDry run: nothing is created; these are the gcloud commands a real run would use.%s\n' "$B" "$N"

# ------------------------------------------------------------------ project
step "Project $PROJECT"
if [ "$DRY" = 0 ]; then
  command -v gcloud >/dev/null || fail "gcloud is not installed here" "use Cloud Shell (console.cloud.google.com, the >_ button), or install the Google Cloud CLI"
  gcloud projects describe "$PROJECT" --format='value(projectId)' >/dev/null 2>&1 ||
    fail "project $PROJECT was not found, or this account cannot see it" "gcloud projects list shows your projects; gcloud auth login switches accounts"
fi
if ! run services enable compute.googleapis.com iap.googleapis.com --project "$PROJECT"; then
  fail "Compute Engine could not be switched on for $PROJECT" "the project needs a billing account (Billing in the console; a Free Trial account works), then run the command again"
fi
ok "Compute Engine and Identity-Aware Proxy are on"

# ------------------------------------------------------------------ network
step "Network $NETWORK (only what the rules below allow gets in)"
if exists compute networks describe "$NETWORK" --project "$PROJECT"; then
  ok "network $NETWORK exists"
else
  run compute networks create "$NETWORK" --project "$PROJECT" --subnet-mode=custom --quiet
  ok "network $NETWORK created"
fi
if exists compute networks subnets describe "$SUBNET" --region "$REGION" --project "$PROJECT"; then
  ok "subnet $SUBNET exists"
else
  run compute networks subnets create "$SUBNET" --project "$PROJECT" --network "$NETWORK" --region "$REGION" --range "$RANGE" --quiet
  ok "subnet $SUBNET created ($RANGE)"
fi

step "Firewall rules"
rule "$NETWORK-ssh-iap" tcp:22 "$IAP_RANGE" "SSH through Identity-Aware Proxy"
if [ -n "$SSH_FROM" ]; then rule "$NETWORK-ssh-direct" tcp:22 "$SSH_FROM" "SSH from your own client"; else drop_rule "$NETWORK-ssh-direct"; fi
if [ -n "$COLLECTORS" ]; then rule "$NETWORK-collectors" tcp:10051 "$COLLECTORS" "collectors at customer sites"; else drop_rule "$NETWORK-collectors"; fi
info "Nothing else gets in: browsers come through the Cloudflare Tunnel, which connects out."

# ------------------------------------------------------------------ virtual machine
step "Virtual machine $NAME"
if exists compute instances describe "$NAME" --zone "$ZONE" --project "$PROJECT"; then
  ok "virtual machine $NAME exists ($(gcloud compute instances describe "$NAME" --zone "$ZONE" --project "$PROJECT" --format='value(status)'))"
else
  args=(compute instances create "$NAME" --project "$PROJECT" --zone "$ZONE" --machine-type "$MACHINE"
        --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud
        --boot-disk-size "${DISK}GB" --boot-disk-type pd-balanced
        --network "$NETWORK" --subnet "$SUBNET" --tags "$TAG" --quiet)
  if ! run "${args[@]}"; then
    fail "the virtual machine was not created (Google's reason is above)" \
      "a quota or a missing machine type: another --machine or --zone; a new project: wait a minute after the step above and run the command again"
  fi
  ok "virtual machine $NAME created: $MACHINE, Ubuntu 24.04, ${DISK} GB"
fi
if [ -n "$SSH_FROM" ]; then
  # The guest agent turns this metadata into a Linux account with sudo and the key. A file, because
  # --metadata splits its value at commas.
  keyfile=$(mktemp)
  printf '%s:%s\n' "$LINUX_USER" "$(head -n 1 "$PUBKEY")" > "$keyfile"
  run compute instances add-metadata "$NAME" --zone "$ZONE" --project "$PROJECT" \
    --metadata-from-file "ssh-keys=$keyfile" --quiet
  rm -f "$keyfile"
  ok "SSH key of $LINUX_USER added"
fi
ADDRESS="<its external address>"
if [ "$DRY" = 0 ]; then
  ADDRESS=$(gcloud compute instances describe "$NAME" --zone "$ZONE" --project "$PROJECT" \
    --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
fi

# ------------------------------------------------------------------ next
step "Next"
info "Connect (the first time gcloud makes an SSH key; press Enter at its questions):"
info "  gcloud compute ssh $NAME --project $PROJECT --zone $ZONE --tunnel-through-iap"
if [ -n "$SSH_FROM" ]; then info "or from your own client:  ssh $LINUX_USER@$ADDRESS"; fi
info "Then follow environments/gcp/README.md from step 4, or the install guide with"
info "Where the server runs: Google Cloud. On the server, prepare-server.sh takes --no-firewall:"
info "this network's firewall protects the server."
info "Stop it when you are not testing (the disk still costs a little):"
info "  gcloud compute instances stop $NAME --zone $ZONE --project $PROJECT"
