#!/usr/bin/env bash
# Create or update the secrets folder of one customer: device log-in and SNMP credentials.
# The values never appear on screen, in the shell history or in Git.
#
# Usage: ./scripts/add-customer-secrets.sh <customer code>
#   <customer code>: the same short code you will type into the Nautobot tenant field msp_code,
#   lowercase, starting with a letter, 2 to 20 characters of a-z, 0-9 and -, for example acme.
set -euo pipefail
cd "$(dirname "$0")/.."

code=${1:-}
if ! [[ "$code" =~ ^[a-z][a-z0-9-]{1,19}$ ]]; then
  echo "Usage: $0 <customer code>   (for example: $0 acme)" >&2
  echo "The code is lowercase, starts with a letter, and has 2 to 20 characters of a-z, 0-9 and -." >&2
  exit 1
fi
dir=secrets/tenants/$code
owner=$(stat -c %U .)
if [ "$(id -un)" != "$owner" ]; then
  echo "Run this without sudo, as $owner: the kit folder belongs to $owner." >&2
  [ "${SUDO_USER:-}" = "$owner" ] ||
    echo "To use this account instead, make it the owner first: sudo chown -R $(id -un): $(pwd)" >&2
  exit 1
fi

ask() {  # ask VARIABLE "question" [hidden]
  local value=""
  if [ "${3:-}" = hidden ]; then
    read -r -s -p "  $2: " value
    echo
  else
    read -r -p "  $2: " value
  fi
  printf -v "$1" '%s' "$value"
}
write() {  # write FILE VALUE (no trailing newline; readable by the platform containers)
  printf '%s' "$2" > "$dir/$1"
  chmod 644 "$dir/$1"
}

echo "Secrets for customer '$code' (stored in $dir; typing is hidden for passwords)"
mkdir -p "$dir"
chmod 755 secrets secrets/tenants "$dir"

ask user "Device log-in user name for SSH, for example netadmin"
ask pass "Device log-in password" hidden
ask pass2 "Same password again" hidden
[ -n "$user" ] && [ -n "$pass" ] || { echo "The user name and the password are required." >&2; exit 1; }
[ "$pass" = "$pass2" ] || { echo "The two passwords differ. Nothing was saved." >&2; exit 1; }
ask enable "Enable secret for Cisco devices (press Enter if none)" hidden

ask version "SNMP version this customer's devices use, 2 or 3"
case "$version" in
  2)
    ask community "SNMP v2c read-only community" hidden
    [ -n "$community" ] || { echo "The community is required for SNMP v2." >&2; exit 1; }
    ;;
  3)
    ask v3user "SNMP v3 user name"
    ask v3auth "SNMP v3 authentication pass phrase (SHA)" hidden
    ask v3priv "SNMP v3 privacy pass phrase (AES)" hidden
    [ -n "$v3user" ] && [ ${#v3auth} -ge 8 ] && [ ${#v3priv} -ge 8 ] ||
      { echo "SNMP v3 needs a user name and two pass phrases of at least 8 characters." >&2; exit 1; }
    ;;
  *) echo "Answer 2 or 3." >&2; exit 1 ;;
esac

write device_username "$user"
write device_password "$pass"
if [ -n "$enable" ]; then write device_enable "$enable"; else rm -f "$dir/device_enable"; fi
if [ "$version" = 2 ]; then
  write snmp_community "$community"
  rm -f "$dir/snmpv3_user" "$dir/snmpv3_authpass" "$dir/snmpv3_privpass"
else
  write snmpv3_user "$v3user"
  write snmpv3_authpass "$v3auth"
  write snmpv3_privpass "$v3priv"
  rm -f "$dir/snmp_community"
fi

echo
echo "Saved: $(cd "$dir" && ls | tr '\n' ' ')"
echo "Next: in Nautobot, create the tenant with msp_code = $code and SNMP version = $version"
echo "(or, if the tenant already exists: docker compose exec msp-glue mspctl tenant sync $code)."
