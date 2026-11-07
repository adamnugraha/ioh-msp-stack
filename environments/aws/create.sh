#!/usr/bin/env bash
# =============================================================================
# Create the platform's server on AWS: one Ubuntu 24.04 instance in the Region's default network,
# whose security group lets in SSH from your own address, and with direct access the web on ports 80
# and 443. Run it in AWS CloudShell (the >_ button in the AWS console), where the AWS CLI is ready, or
# on a PC with AWS CLI v2.
#
#   ./environments/aws/create.sh --ssh-from <your address>/32
#
# Options:
#   --ssh-from "CIDR ..."  where your SSH client connects from: open https://checkip.amazonaws.com
#                          on your PC (or on the server that runs your SSH client, such as Termix)
#   --region REGION        default ap-southeast-3 (Jakarta; an opt-in Region, see the README)
#   --name NAME            the instance, its key pair and its security group (default msp-test-01)
#   --type TYPE            default m7i-flex.xlarge (4 vCPU, 16 GB); m7i-flex.large (2 vCPU, 8 GB)
#                          is one of the sizes the free plan allows
#   --disk GB              default 100, at least 40
#   --public-key FILE      log in with your own SSH key; without it the script makes a key pair
#                          and saves its private key as NAME.pem here
#   --collectors-from "CIDR ..."  open port 10051 to collectors at these addresses
#   --autoinstall FILE     the instance then installs the whole platform by itself, with the
#                          settings in FILE (environments/autoinstall.conf.example); 40 to 75 minutes
#   --way-in WAY           cloudflare (a Cloudflare Tunnel, no web port open) or direct (ports 80 and
#                          443 open, an Elastic IP NAME-ip); default: WAY_IN in the settings file,
#                          else cloudflare
#   --dry-run              print the AWS CLI commands without running them
#
# Running it again is safe: what exists is kept; new --ssh-from addresses are added.
# ./environments/aws/delete.sh removes everything this script created (the Elastic IP only with
# --release-address, so a rebuild keeps it and its DNS record).
# =============================================================================
set -euo pipefail
export AWS_PAGER=""

SSH_FROM="" REGION=ap-southeast-3 NAME=msp-test-01 TYPE=m7i-flex.xlarge DISK=100 PUBKEY="" COLLECTORS="" AUTOCONF="" WAYIN="" DRY=0
AMI_PARAM=/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; Y=; R=; N=; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %sOK%s     %s\n' "$G" "$N" "$*"; }
info() { printf '         %s\n' "$*"; }
warn() { printf '  %sWARN%s   %s\n' "$Y" "$N" "$1"; [ -n "${2:-}" ] && printf '         Fix: %s\n' "$2"; return 0; }
fail() { printf '\n  %sSTOPPED%s %s\n' "$R" "$N" "$1"; [ -n "${2:-}" ] && printf '  Fix:    %s\n' "$2"; exit 1; }
usage() { sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ssh-from) SSH_FROM=${2:-}; shift 2 ;;
    --region) REGION=${2:-}; shift 2 ;;
    --name) NAME=${2:-}; shift 2 ;;
    --type) TYPE=${2:-}; shift 2 ;;
    --disk) DISK=${2:-}; shift 2 ;;
    --public-key) PUBKEY=${2:-}; shift 2 ;;
    --collectors-from) COLLECTORS=${2:-}; shift 2 ;;
    --autoinstall) AUTOCONF=${2:-}; shift 2 ;;
    --way-in) WAYIN=${2:-}; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "unknown option '$1'" "./environments/aws/create.sh --help lists the options" ;;
  esac
done

# ------------------------------------------------------------------ check the options
[ -n "$SSH_FROM" ] || fail "--ssh-from is missing: without it nobody could log in" \
  "open https://checkip.amazonaws.com on your PC and add --ssh-from <that address>/32"
python3 - "$SSH_FROM" "$REGION" "$NAME" "$TYPE" "$DISK" "$COLLECTORS" <<'PY' || fail "an option is not valid (see above)" "correct it and run the command again"
import ipaddress, re, sys
ssh_from, region, name, itype, disk, collectors = sys.argv[1:7]
errors = []
for flag, value in (("--ssh-from", ssh_from), ("--collectors-from", collectors)):
    for item in value.split():
        try:
            net = ipaddress.ip_network(item, strict=False)
            if net.prefixlen == 0:
                errors.append(f"{flag}: {item} would let the whole Internet in; give your own address with /32")
        except ValueError:
            errors.append(f"{flag}: '{item}' is not an address range such as 203.0.113.7/32")
if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]", region):
    errors.append(f"--region '{region}' is not a Region such as ap-southeast-3")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", name):
    errors.append(f"--name '{name}': letters, digits and dashes")
if not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9]+", itype):
    errors.append(f"--type '{itype}' is not an instance type such as m7i-flex.xlarge")
if not disk.isdigit() or int(disk) < 40:
    errors.append(f"--disk '{disk}': a number of GB, 40 or more")
for e in errors:
    print("  FAIL   " + e)
sys.exit(1 if errors else 0)
PY
# The way in: --way-in, else the settings file's WAY_IN, else the Cloudflare Tunnel.
KIT=$(cd "$(dirname "$0")/../.." && pwd)
OVERRIDE=()
case "$WAYIN" in
  "") ;;
  cloudflare | direct) OVERRIDE=("WAY_IN=$WAYIN") ;;
  *) fail "--way-in '$WAYIN': cloudflare or direct" "./environments/aws/create.sh --help lists the options" ;;
esac
DOMAIN="<your domain>"
if [ -n "$AUTOCONF" ]; then
  "$KIT/environments/bootstrap.sh" --check "$AUTOCONF" ENVIRONMENT=aws "${OVERRIDE[@]}" || exit 1   # it says what to correct
  WAYIN=$(python3 "$KIT/scripts/autoinstall-settings.py" "$AUTOCONF" ENVIRONMENT=aws "${OVERRIDE[@]}" --get WAY_IN)
  DOMAIN=$(python3 "$KIT/scripts/autoinstall-settings.py" "$AUTOCONF" ENVIRONMENT=aws --get DOMAIN)
fi
WAYIN=${WAYIN:-cloudflare}
if [ -n "$PUBKEY" ]; then
  if ! { [ -r "$PUBKEY" ] && grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp[0-9]+) ' "$PUBKEY"; }; then
    fail "$PUBKEY is not an SSH public key" "give the .pub file, the one that starts with ssh-ed25519 or ssh-rsa"
  fi
fi

aws_() {  # aws_ ARGS...: the AWS CLI in the chosen Region, or only show the command in a dry run
  if [ "$DRY" = 1 ]; then printf '  would run: aws'; printf ' %q' "$@" --region "$REGION"; printf '\n'; return 0; fi
  aws "$@" --region "$REGION"
}
ask() {  # ask ARGS...: a read-only query (empty in a dry run); prints the answer, "None" becomes empty
  [ "$DRY" = 1 ] && return 0
  local out; out=$(aws "$@" --region "$REGION" --output text 2>/dev/null || true)
  [ "$out" = None ] && out=""
  printf '%s' "$out"
}

[ "$DRY" = 1 ] && printf '%sDry run: nothing is created; these are the AWS CLI commands a real run would use.%s\n' "$B" "$N"

# ------------------------------------------------------------------ account and Region
step "Account and Region $REGION"
[ "$DRY" = 1 ] && info "(the account, the Region and the Ubuntu image are only read in a real run)"
if [ "$DRY" = 0 ]; then
  command -v aws >/dev/null || fail "the AWS CLI is not installed here" "use AWS CloudShell (the >_ button in the AWS console), or install AWS CLI v2"
  account=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) ||
    fail "the AWS CLI has no working credentials here" "in CloudShell they are there already; on a PC run aws configure or aws sso login"
  ok "account $account"
  opt=$(aws account get-region-opt-status --region-name "$REGION" --query RegionOptStatus --output text 2>/dev/null || true)
  case "$opt" in
    ENABLED | ENABLED_BY_DEFAULT) ok "Region $REGION is enabled" ;;
    ENABLING) fail "Region $REGION is still being enabled" "wait until it is done (minutes, sometimes hours), then run the command again" ;;
    DISABLED | DISABLING) fail "Region $REGION is not enabled for this account (Jakarta is an opt-in Region)" \
      "aws account enable-region --region-name $REGION, or the console: Account, AWS Regions; wait until it shows Enabled, then run the command again" ;;
    *) warn "could not read whether $REGION is enabled; going on" "if the next step says AuthFailure or OptInRequired, enable the Region first (see the README)" ;;
  esac
fi
AMI=$(ask ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value)
if [ "$DRY" = 0 ]; then
  [ -n "$AMI" ] || fail "Ubuntu 24.04 is not listed in $REGION" "the Region is not enabled yet, or choose another --region"
  ok "Ubuntu 24.04 image $AMI (Canonical's latest)"
fi

# ------------------------------------------------------------------ network
step "Network"
read -r VPC VPC_CIDR <<<"$(ask ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].[VpcId,CidrBlock]')" || true
if [ "$DRY" = 0 ]; then
  [ -n "${VPC:-}" ] || fail "$REGION has no default network (VPC) in this account" "aws ec2 create-default-vpc --region $REGION, then run the command again"
  ok "default network $VPC ($VPC_CIDR)"
fi
# Docker's address pool on the server must stay clear of the network: AWS's default 172.31.0.0/16
# is the kit's usual pool, so pick another.
POOL=$(python3 - "${VPC_CIDR:-172.31.0.0/16}" <<'PY'
import ipaddress, sys
vpc = ipaddress.ip_network(sys.argv[1])
for c in ("172.29.0.0/16", "172.28.0.0/16", "172.27.0.0/16", "10.250.0.0/16"):
    if not ipaddress.ip_network(c).overlaps(vpc):
        print(c)
        break
PY
)
clear_of=${VPC_CIDR:-172.31.0.0/16}
info "Docker's address pool on this server: $POOL (clear of the network's $clear_of)"

# ------------------------------------------------------------------ key pair
step "Key pair $NAME"
KEYFILE=""
have_key=$(ask ec2 describe-key-pairs --key-names "$NAME" --query 'KeyPairs[0].KeyName')
if [ -n "$have_key" ]; then
  ok "key pair $NAME exists in AWS"
  if [ -z "$PUBKEY" ]; then
    KEYFILE="$NAME.pem"
    [ -f "$KEYFILE" ] || warn "the private key $KEYFILE is not in this folder" \
      "use the copy you saved when it was made; if it is lost, delete the key pair (aws ec2 delete-key-pair --key-name $NAME) and the instance, then run this again"
  fi
elif [ -n "$PUBKEY" ]; then
  aws_ ec2 import-key-pair --key-name "$NAME" --public-key-material "fileb://$PUBKEY" --query KeyName --output text
  ok "your key $PUBKEY imported as $NAME"
else
  KEYFILE="$NAME.pem"
  [ -e "$KEYFILE" ] && fail "$KEYFILE exists here already, from another key pair" "move it away first, then run the command again"
  if [ "$DRY" = 1 ]; then
    aws_ ec2 create-key-pair --key-name "$NAME" --key-type ed25519 --query KeyMaterial --output text
  else
    (umask 077; aws ec2 create-key-pair --key-name "$NAME" --key-type ed25519 --query KeyMaterial --output text --region "$REGION" > "$KEYFILE")
    chmod 400 "$KEYFILE"
  fi
  ok "key pair $NAME made; its private key is $PWD/$KEYFILE"
  info "Download it to your PC now (CloudShell: Actions, Download file); AWS never shows it again."
fi

# ------------------------------------------------------------------ security group
step "Security group $NAME-sg (only what is listed here gets in)"
SG=$(ask ec2 describe-security-groups --filters "Name=group-name,Values=$NAME-sg" "Name=vpc-id,Values=${VPC:-vpc}" --query 'SecurityGroups[0].GroupId')
if [ -n "$SG" ]; then
  ok "security group $SG exists"
else
  if [ "$DRY" = 1 ]; then
    aws_ ec2 create-security-group --group-name "$NAME-sg" --description "IOH MSP platform $NAME (environments/aws/create.sh)" --vpc-id "${VPC:-<default VPC>}"
    SG="<security group>"
  else
    SG=$(aws ec2 create-security-group --group-name "$NAME-sg" --description "IOH MSP platform $NAME (environments/aws/create.sh)" \
      --vpc-id "$VPC" --query GroupId --output text --region "$REGION")
  fi
  ok "security group $SG created"
fi
allow() {  # allow PORT CIDR WHAT: one inbound rule, skipped when it is there already
  local out
  if [ "$DRY" = 1 ]; then aws_ ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port "$1" --cidr "$2"; return 0; fi
  if out=$(aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port "$1" --cidr "$2" --region "$REGION" 2>&1); then
    ok "port $1 from $2 ($3)"
  elif printf '%s' "$out" | grep -q InvalidPermission.Duplicate; then
    ok "port $1 from $2 ($3), there already"
  else
    printf '%s\n' "$out"
    fail "the rule for port $1 from $2 was refused" "read AWS's reason above"
  fi
}
disallow() {  # disallow PORT CIDR: remove one inbound rule, when it is there
  if [ "$DRY" = 1 ]; then return 0; fi
  if aws ec2 revoke-security-group-ingress --group-id "$SG" --protocol tcp --port "$1" --cidr "$2" --region "$REGION" >/dev/null 2>&1; then
    ok "port $1 from $2 removed (the way in is the Cloudflare Tunnel now)"
  fi
}
for c in $SSH_FROM; do allow 22 "$c" "SSH"; done
for c in $COLLECTORS; do allow 10051 "$c" "collector at a customer site"; done
if [ "$WAYIN" = direct ]; then
  allow 80 0.0.0.0/0 "browsers, and Let's Encrypt's checks"
  allow 443 0.0.0.0/0 "browsers"
  info "Nothing else gets in. The sign-in gate on the server keeps the engineers' tools private."
else
  disallow 80 0.0.0.0/0
  disallow 443 0.0.0.0/0
  info "Nothing else gets in: browsers come through the Cloudflare Tunnel, which connects out."
fi
info "An address you no longer use stays allowed until you remove it (EC2, Security groups, $NAME-sg)."

# ------------------------------------------------------------------ Elastic IP (direct access)
EIP="" EIP_ALLOC=""
if [ "$WAYIN" = direct ]; then
  step "Elastic IP $NAME-ip (the same after a stop, a start or a rebuild)"
  read -r EIP_ALLOC EIP <<<"$(ask ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME-ip" --query 'Addresses[0].[AllocationId,PublicIp]')" || true
  if [ -n "$EIP_ALLOC" ]; then
    ok "Elastic IP $NAME-ip exists: $EIP"
  elif [ "$DRY" = 1 ]; then
    aws_ ec2 allocate-address --domain vpc --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME-ip}]"
    EIP="<the Elastic IP>" EIP_ALLOC="<allocation ID>"
  else
    read -r EIP_ALLOC EIP <<<"$(aws ec2 allocate-address --domain vpc --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME-ip}]" \
      --query '[AllocationId,PublicIp]' --output text --region "$REGION")" || true
    [ -n "$EIP" ] || fail "AWS gave no Elastic IP" "an account may hold 5 per Region: release one you no longer use (EC2, Elastic IPs), then run this again"
    ok "Elastic IP $EIP allocated as $NAME-ip"
  fi
fi

# ------------------------------------------------------------------ instance
step "Instance $NAME"
ID=$(ask ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId')
if [ -n "$ID" ]; then
  ok "instance $ID exists ($(ask ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].State.Name'))"
  if [ -n "$AUTOCONF" ]; then
    warn "the automatic install runs only at an instance's first start, so nothing changes on $NAME" \
      "for a fresh install: ./environments/aws/delete.sh, then this command again"
  fi
else
  # The automatic install: user data made from the settings file, with the pool chosen above.
  USERDATA=""
  if [ -n "$AUTOCONF" ]; then
    USERDATA=$(mktemp)
    trap 'rm -f "$USERDATA"' EXIT
    "$KIT/environments/bootstrap.sh" "$AUTOCONF" ENVIRONMENT=aws POOL="$POOL" "${OVERRIDE[@]}" > "$USERDATA" || exit 1
    [ "$(wc -c < "$USERDATA")" -le 16384 ] || fail "the start script is larger than AWS's 16 KB for user data" \
      "shorten long values in $AUTOCONF"
  fi
  args=(ec2 run-instances --image-id "${AMI:-<Ubuntu 24.04 image>}" --instance-type "$TYPE" --key-name "$NAME"
        --security-group-ids "$SG"
        --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$DISK,VolumeType=gp3,DeleteOnTermination=true}"
        --metadata-options "HttpTokens=required,HttpEndpoint=enabled"
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" "ResourceType=volume,Tags=[{Key=Name,Value=$NAME}]"
        --query 'Instances[0].InstanceId' --output text)
  if [ -n "$USERDATA" ]; then args+=(--user-data "file://$USERDATA"); fi
  if [ "$DRY" = 1 ]; then
    aws_ "${args[@]}"
  else
    if ! ID=$(aws "${args[@]}" --region "$REGION" 2>&1); then
      printf '%s\n' "$ID"
      fail "the instance was not started (AWS's reason is above)" \
        "on the free plan, use --type m7i-flex.large, or upgrade to the paid plan (the credits stay); another type if $TYPE is not offered in $REGION"
    fi
    ok "instance $ID started: $TYPE, Ubuntu 24.04, ${DISK} GB"
    if [ -n "$USERDATA" ]; then ok "the automatic install starts with the instance (settings from $AUTOCONF)"; fi
    info "waiting until it runs..."
    aws ec2 wait instance-running --instance-ids "$ID" --region "$REGION"
  fi
fi
if [ "$WAYIN" = direct ]; then
  if [ "$DRY" = 1 ]; then
    aws_ ec2 associate-address --allocation-id "$EIP_ALLOC" --instance-id "${ID:-<instance ID>}" --allow-reassociation
  elif [ "$(ask ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].InstanceId')" != "$ID" ]; then
    aws ec2 associate-address --allocation-id "$EIP_ALLOC" --instance-id "$ID" --allow-reassociation --region "$REGION" >/dev/null ||
      fail "the Elastic IP $EIP could not be given to $ID (AWS's reason is above)" "run the command again in a minute"
    ok "Elastic IP $EIP now belongs to $ID"
  else
    ok "Elastic IP $EIP belongs to $ID"
  fi
fi
IP=$(ask ec2 describe-instances --instance-ids "${ID:-i-none}" --query 'Reservations[0].Instances[0].PublicIpAddress')
if [ "$WAYIN" = direct ]; then IP=$EIP; fi
if [ "$DRY" = 1 ]; then IP=${EIP:-<its public address>}; ID="<instance ID>"; fi

# ------------------------------------------------------------------ next
step "Next"
LOGIN="ssh ubuntu@$IP"
if [ -n "$KEYFILE" ]; then LOGIN="ssh -i $KEYFILE ubuntu@$IP"; fi
info "Connect from your PC:  $LOGIN"
if [ "$WAYIN" = direct ]; then
  info ""
  info "DNS, now: at your DNS provider, point every name under the domain at $EIP. One record does it:"
  info "  *.$DOMAIN   A   $EIP       (on Cloudflare: DNS only, the grey cloud)"
  info "The install waits for it before Let's Encrypt is asked for certificates. The Elastic IP stays the"
  info "instance's for good: delete.sh keeps it for a rebuild, so the record stays right."
  info ""
fi
if [ -n "$AUTOCONF" ]; then
  info "The instance installs the platform by itself, 40 to 75 minutes. How far it is, any time:"
  info "  $LOGIN sudo /opt/ioh-msp-stack/scripts/autoinstall.sh status"
  info "Once finished, the same command shows the first sign-in. A rebuild later:"
  info "  ./environments/aws/delete.sh, then this create command again"
else
  info "Then follow environments/aws/README.md from step 5, or the install guide with"
  info "Where the server runs: AWS. On the server, prepare-server.sh takes --no-firewall --pool $POOL:"
  info "the security group protects the server, and the pool stays clear of AWS's network."
fi
if [ "$WAYIN" != direct ]; then
  info "The public address changes after a stop and start; this shows the current one:"
  info "  aws ec2 describe-instances --instance-ids $ID --region $REGION --query 'Reservations[0].Instances[0].PublicIpAddress' --output text"
fi
info "Stop it when you are not testing (the disk still costs a little):"
info "  aws ec2 stop-instances --instance-ids $ID --region $REGION"
