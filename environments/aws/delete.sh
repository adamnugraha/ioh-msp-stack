#!/usr/bin/env bash
# =============================================================================
# Remove what environments/aws/create.sh made: the instance with its disk, its security group and
# its key pair. Everything on the server is lost; back up first if anything there matters
# (sudo ./scripts/backup.sh, then copy /var/backups/msp off the server). The Elastic IP (NAME-ip,
# direct access) stays for a rebuild, so its DNS record stays right; --release-address releases it too.
#
#   ./environments/aws/delete.sh
#
# Options:
#   --region REGION      default ap-southeast-3
#   --name NAME          default msp-test-01
#   --release-address    release the Elastic IP NAME-ip as well (when the trial is over)
#   --yes                do not ask to type the name first
# =============================================================================
set -euo pipefail
export AWS_PAGER=""

REGION=ap-southeast-3 NAME=msp-test-01 YES=0 RELEASE=0

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; R=; N=; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %sOK%s     %s\n' "$G" "$N" "$*"; }
info() { printf '         %s\n' "$*"; }
fail() { printf '\n  %sSTOPPED%s %s\n' "$R" "$N" "$1"; [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2"; exit 1; }
usage() { sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --region) REGION=${2:-}; shift 2 ;;
    --name) NAME=${2:-}; shift 2 ;;
    --release-address) RELEASE=1; shift ;;
    --yes) YES=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "unknown option '$1'" "./environments/aws/delete.sh --help lists the options" ;;
  esac
done
command -v aws >/dev/null || fail "the AWS CLI is not installed here" "use AWS CloudShell, or install AWS CLI v2"
q() { local out; out=$(aws "$@" --region "$REGION" --output text 2>/dev/null || true); [ "$out" = None ] && out=""; printf '%s' "$out"; }

if [ "$YES" = 0 ]; then
  printf 'This deletes the instance %s in %s, with its disk and everything on it.\n' "$NAME" "$REGION"
  printf 'Type its name to go on: '
  read -r answer
  [ "$answer" = "$NAME" ] || fail "the name did not match, so nothing was deleted" "run the command again and type $NAME"
fi

step "Instance $NAME"
ids=$(q ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId')
if [ -n "$ids" ]; then
  # shellcheck disable=SC2086 # one or more instance IDs, separated by spaces or tabs
  aws ec2 terminate-instances --instance-ids $ids --region "$REGION" >/dev/null
  info "waiting until it is gone..."
  # shellcheck disable=SC2086
  aws ec2 wait instance-terminated --instance-ids $ids --region "$REGION"
  ok "instance $ids and its disk deleted"
else
  ok "no instance $NAME"
fi

step "Security group and key pair"
sg=$(q ec2 describe-security-groups --filters "Name=group-name,Values=$NAME-sg" --query 'SecurityGroups[0].GroupId')
if [ -n "$sg" ]; then
  aws ec2 delete-security-group --group-id "$sg" --region "$REGION"
  ok "security group $sg deleted"
fi
if [ -n "$(q ec2 describe-key-pairs --key-names "$NAME" --query 'KeyPairs[0].KeyName')" ]; then
  aws ec2 delete-key-pair --key-name "$NAME" --region "$REGION"
  ok "key pair $NAME deleted (delete $NAME.pem on your PC too)"
  # Its private key here is useless now, and create.sh makes a new one under the same name.
  if [ -f "$NAME.pem" ]; then rm -f "$NAME.pem"; ok "$NAME.pem in this folder deleted"; fi
fi
# The Elastic IP (direct access): free again once its instance is gone; kept for a rebuild unless asked.
read -r alloc eip <<<"$(q ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME-ip" --query 'Addresses[0].[AllocationId,PublicIp]')" || true
if [ -n "${alloc:-}" ]; then
  if [ "$RELEASE" = 1 ]; then
    aws ec2 release-address --allocation-id "$alloc" --region "$REGION"
    ok "Elastic IP $NAME-ip ($eip) released: delete its DNS records too"
  else
    ok "Elastic IP $NAME-ip ($eip) kept for a rebuild (about US\$3.60 a month)"
    info "When the trial is over: the same command with --release-address releases it."
  fi
fi
info "With a Cloudflare Tunnel, the tunnel, its routes and Cloudflare Access stay in Cloudflare: a rebuild"
info "(create.sh again) uses them as they are. To remove them too: Networking, Tunnels, and Zero Trust, Access."
info "With direct access, the DNS records stay at your DNS provider, pointing at the kept Elastic IP."
