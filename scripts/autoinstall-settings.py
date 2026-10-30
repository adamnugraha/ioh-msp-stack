#!/usr/bin/env python3
"""Read and check the settings file of the automatic install, without running it as shell code.

    autoinstall-settings.py FILE [--server] [KEY=VALUE ...]          every value, NUL-separated
    autoinstall-settings.py FILE --clean [KEY=VALUE ...]             the file as KEY='VALUE' lines

Lines are KEY=VALUE; a value with spaces needs quotes; # starts a note. KEY=VALUE arguments set or
replace a value. --server also checks what only the server can see (time zones, the proxy CA file).
Problems go to stderr as FAIL lines, with exit code 1. scripts/autoinstall.sh (on the server) and
environments/bootstrap.sh (where the start script is made) both read the file through this script.
"""
import ipaddress
import os
import re
import shlex
import sys

DEFAULTS = {
    "ENVIRONMENT": "", "DOMAIN": "", "STAFF_EMAIL": "", "STAFF_NAME": "", "NOC_EMAIL": "",
    "CLOUDFLARE_TUNNEL_TOKEN": "", "SAMPLES": "no", "SSO_TOOLS": "yes", "GRAFANA": "no", "BACKUP": "yes",
    "TIMEZONE": "Asia/Jakarta", "SUBNET": "172.30.0.0/24", "POOL": "172.31.0.0/16",
    "SSH_FROM": "", "ADMIN_NETWORKS": "", "PROXY": "", "PROXY_CA": "",
    "DOCKERHUB_USER": "", "DOCKERHUB_TOKEN": "", "INSTALL_USER": "msp", "KIT_URL": "", "KIT_SHA256": "",
}
EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"


def parse(path, overrides, errors):
    given = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        errors.append(f"cannot read {path}: {e.strerror}")
        return given
    numbered = [(f"line {n}", line) for n, line in enumerate(lines, 1)] + [("argument", o) for o in overrides]
    for where, raw in numbered:
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if not m:
            errors.append(f"{where}: not KEY=VALUE: {line[:60]}")
            continue
        key, value = m.groups()
        if where == "argument":  # KEY=VALUE given on the command line: the value as it is
            parts = [raw.split("=", 1)[1]]
        else:
            try:
                parts = shlex.split(value, comments=True)
            except ValueError as e:
                errors.append(f"{where}: {key}: {e} (a quote opened and not closed?)")
                continue
        if key not in DEFAULTS:
            errors.append(f"{where}: unknown setting {key} (environments/autoinstall.conf.example lists them)")
            continue
        if len(parts) > 1:
            errors.append(f'{where}: {key}: a value with spaces needs quotes: {key}="{" ".join(parts)}"')
            continue
        given[key] = parts[0] if parts else ""
    return given


def check(v, server, errors):
    env = v["ENVIRONMENT"]
    if env not in ("gcp", "aws", "vps", "ioh"):
        errors.append(f"ENVIRONMENT '{env}': gcp, aws, vps or ioh")
    if not re.fullmatch(r"(?=.{1,200}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", v["DOMAIN"]):
        errors.append(f"DOMAIN '{v['DOMAIN']}' is not a lowercase DNS domain such as example.com")
    if not re.fullmatch(EMAIL, v["STAFF_EMAIL"]):
        errors.append(f"STAFF_EMAIL '{v['STAFF_EMAIL']}' is not an e-mail address")
    if not re.fullmatch(r'[^"\\$`\n]{2,80}', v["STAFF_NAME"].strip()):
        errors.append("STAFF_NAME: a name of 2 to 80 characters, without quotes, backslashes or the $ sign")
    if v["NOC_EMAIL"] and not re.fullmatch(EMAIL, v["NOC_EMAIL"]):
        errors.append(f"NOC_EMAIL '{v['NOC_EMAIL']}' is not an e-mail address")
    tz = v["TIMEZONE"]
    if not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}", tz) or (server and not os.path.exists("/usr/share/zoneinfo/" + tz)):
        errors.append(f"TIMEZONE '{tz}' is not a time zone such as Asia/Jakarta")
    net = None
    try:
        net = ipaddress.ip_network(v["SUBNET"])
        if net.prefixlen > 26:
            errors.append(f"SUBNET {v['SUBNET']}: use a /24 (at least a /26)")
    except ValueError:
        errors.append(f"SUBNET '{v['SUBNET']}' is not a network such as 172.30.0.0/24")
    try:
        pool = ipaddress.ip_network(v["POOL"])
        if not 16 <= pool.prefixlen <= 20:
            errors.append(f"POOL {v['POOL']}: a /16 to /20")
        if net and pool.overlaps(net):
            errors.append(f"POOL {v['POOL']} overlaps SUBNET {v['SUBNET']}")
    except ValueError:
        errors.append(f"POOL '{v['POOL']}' is not a network such as 172.31.0.0/16")
    for name, required in (("SSH_FROM", env == "vps"), ("ADMIN_NETWORKS", env == "ioh")):
        if not v[name].strip():
            if required:
                errors.append(f"{name} is needed when ENVIRONMENT={env}")
            continue
        for item in v[name].split():
            try:
                if ipaddress.ip_network(item, strict=False).prefixlen < 8:
                    errors.append(f"{name}: {item} covers a large part of the Internet")
            except ValueError:
                errors.append(f"{name}: '{item}' is not an address range such as 203.0.113.7/32")
    if v["PROXY"] and not re.fullmatch(r"https?://[^\s/]+(:\d+)?/?", v["PROXY"]):
        errors.append(f"PROXY '{v['PROXY']}' is not a proxy URL such as http://proxy.ioh.internal:8080")
    if server and v["PROXY_CA"] and not os.path.isfile(v["PROXY_CA"]):
        errors.append(f"PROXY_CA {v['PROXY_CA']}: no such file on this server")
    for name in ("SAMPLES", "SSO_TOOLS", "GRAFANA", "BACKUP"):
        if v[name] not in ("yes", "no"):
            errors.append(f"{name} '{v[name]}': yes or no")
    if v["CLOUDFLARE_TUNNEL_TOKEN"] and not re.fullmatch(r"[A-Za-z0-9+/=_-]{50,}", v["CLOUDFLARE_TUNNEL_TOKEN"]):
        errors.append('CLOUDFLARE_TUNNEL_TOKEN is not a tunnel token (the long string after "cloudflared service install")')
    if bool(v["DOCKERHUB_USER"]) != bool(v["DOCKERHUB_TOKEN"]):
        errors.append("DOCKERHUB_USER and DOCKERHUB_TOKEN go together")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", v["INSTALL_USER"]) or v["INSTALL_USER"] == "root":
        errors.append(f"INSTALL_USER '{v['INSTALL_USER']}': a Linux user name other than root, such as msp")
    if v["KIT_URL"] and not re.fullmatch(r"https?://[^\s'\"]+\.zip", v["KIT_URL"]):
        errors.append("KIT_URL is not a link to a .zip file")
    if v["KIT_SHA256"] and not re.fullmatch(r"[0-9a-f]{64}", v["KIT_SHA256"]):
        errors.append("KIT_SHA256 is not a SHA-256 checksum (64 characters 0-9 and a-f)")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    path, rest = args[0], args[1:]
    server = "--server" in rest
    clean = "--clean" in rest
    overrides = [a for a in rest if a not in ("--server", "--clean")]
    errors = []
    given = parse(path, overrides, errors)
    values = dict(DEFAULTS, **given)
    # Someone may paste the whole "sudo cloudflared service install <token>" line: keep the token.
    values["CLOUDFLARE_TUNNEL_TOKEN"] = (values["CLOUDFLARE_TUNNEL_TOKEN"].split() or [""])[-1]
    if not errors:
        check(values, server, errors)
    if errors:
        for e in errors:
            print("  FAIL   " + e, file=sys.stderr)
        return 1
    if clean:
        for key in DEFAULTS:
            if key in given:
                print(f"{key}={shlex.quote(values[key])}")
    else:
        for key in DEFAULTS:
            sys.stdout.write(f"{key}\0{values[key]}\0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
