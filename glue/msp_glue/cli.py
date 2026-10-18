"""mspctl: command line for the integration service.

  mspctl check                          test the connection to every tool
  mspctl bootstrap tokens ...           create service accounts and print API tokens
  mspctl bootstrap platform             create the shared baseline in every tool
  mspctl bootstrap harden               replace default admin passwords, disable GLPI demo accounts
  mspctl bootstrap console              what the MSP Console needs in the tools (run by install.sh console)
  mspctl tenant list                    list managed customers and their provisioning status
  mspctl tenant sync <code>|--all       provision or repair customers
  mspctl device sync [<code>]           sync devices to Zabbix and GLPI (all customers by default)
  mspctl --dry-run device sync [<code>] only report what device sync would change
  mspctl proxy add <code> <proxy-name>  register (or re-key) a customer-site Zabbix proxy and print its settings
  mspctl user add <code> <login> ...    create a customer portal user (GLPI, and Grafana with --grafana)
  mspctl user disable <code> <login>    remove a customer user's access
  mspctl compliance                     open, update or solve configuration-drift tickets
  mspctl sso setup                      create or repair the Keycloak realm "msp" (needs KEYCLOAK_ADMIN_PASSWORD)
  mspctl sso tools <tool ...>           staff sign in to grafana, forgejo, nautobot, semaphore, zabbix through Keycloak
  mspctl sso keys | sync                the realm's public key and certificate | Semaphore project memberships now
  mspctl staff add <email> --role ...   staff account for the MSP Console: manager, engineer, automation or admin
  mspctl staff roles <email> --role ... set exactly these roles (removes the others)
  mspctl staff list | reset <email> | disable <email>
  mspctl chat setup | off | test        chat alerts through Versus Incident (Telegram, Teams or e-mail)
  mspctl serve                          run the webhook receiver and scheduler (container default)
  mspctl console                        run the MSP Console web interface (msp-console container)
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys

from . import bootstrap, chat, clients, compliance, customers, devices, tenants
from .http import APIError
from .locking import exclusive
from .settings import settings


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_check(_args) -> int:
    ok = True
    checks = {
        "Nautobot": lambda: clients.nautobot().request("GET", "status/")["nautobot-version"],
        "Zabbix": lambda: clients.zabbix().version(),
        "GLPI": lambda: _glpi_version(),
        "Semaphore": lambda: clients.semaphore().request("GET", "info").get("version", "?"),
        "Forgejo": lambda: clients.forgejo().request("GET", "version")["version"],
    }
    for name, probe in checks.items():
        try:
            print(f"  OK    {name:<10} {probe()}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  FAIL  {name:<10} {exc}")
    return 0 if ok else 1


def _glpi_version() -> str:
    with clients.glpi() as g:
        return g.request("GET", "getGlpiConfig")["cfg_glpi"]["version"]


def cmd_bootstrap(args) -> int:
    if args.stage == "tokens":
        tokens = bootstrap.create_tokens(
            (args.zabbix_user, args.zabbix_password), (args.glpi_user, args.glpi_password),
            (args.semaphore_user, args.semaphore_password), (args.forgejo_user, args.forgejo_password),
        )
        print("\n# Paste these lines into .env, then restart msp-glue:")
        for key, value in tokens.items():
            print(f"{key}={value}")
        return 0
    if args.stage == "harden":
        result = bootstrap.harden(
            args.zabbix_admin_password or os.environ.get("NEW_ZABBIX_ADMIN_PASSWORD", ""),
            args.glpi_admin_password or os.environ.get("NEW_GLPI_ADMIN_PASSWORD", ""),
        )
        for key, value in result.items():
            print(f"{key:<16} {value}")
        return 0
    if args.stage == "console":
        bootstrap.ensure_glue_user_in_noc(clients.zabbix())
        print("Zabbix: svc-msp-glue is in the NOC user group (names show in GLPI follow-ups)")
        return 0
    bootstrap.platform(webhook_token=args.webhook_token)
    return 0


def cmd_chat(args) -> int:
    if args.action == "setup":
        result = chat.setup()
    elif args.action == "off":
        result = chat.off()
    else:
        result = {"Versus": chat.test()}
    for key, value in result.items():
        print(f"{key:<14} {value}")
    return 0


def cmd_tenant(args) -> int:
    if args.action == "list":
        print(f"{'CODE':<14} {'TIER':<7} {'STATUS':<12} {'GLPI':<6} {'ZABBIX':<8} {'SEMAPHORE':<9} NAME")
        for t in tenants.managed_tenants():
            print(f"{t.code or '-':<14} {t.tier:<7} {t.custom_fields.get('msp_provisioning_status') or '-':<12} "
                  f"{str(t.glpi_entity_id or '-'):<6} {str(t.zabbix_hostgroup_id or '-'):<8} "
                  f"{str(t.semaphore_project_id or '-'):<9} {t.name}")
        return 0
    if args.all:
        targets = tenants.managed_tenants()
        if not targets:
            print(f"No customers yet in tenant group {settings.mappings['tenancy']['tenant_group']}: nothing to do.")
            return 0
    else:
        if not args.code:
            print("Name a customer code (mspctl tenant sync acme) or use --all.")
            return 1
        targets = [tenants.get_tenant(args.code)]
    if not targets or targets[0] is None:
        print(f"Tenant '{args.code}' not found in tenant group {settings.mappings['tenancy']['tenant_group']}")
        return 1
    rc = 0
    for tenant in targets:
        result = tenants.ensure_tenant(tenant)
        print(f"{tenant.code or tenant.name}: {result.get('msp_provisioning_status', 'skipped')} "
              f"{result.get('msp_provisioning_message', '')}")
        rc |= result.get("msp_provisioning_status") != "provisioned"
    return rc


def cmd_device(args) -> int:
    if args.code:
        tenant = tenants.get_tenant(args.code)
        if not tenant:
            print(f"Tenant '{args.code}' not found")
            return 1
        report = devices.sync_tenant(tenant)
    else:
        report = devices.sync_all()
    print(report.summary())
    for line in report.errors:
        print("  ERROR", line)
    if args.verbose:
        for kind in ("created", "updated", "disabled", "skipped"):
            for line in getattr(report, kind):
                print(f"  {kind:<8} {line}")
    return 1 if report.errors else 0


def cmd_proxy(args) -> int:
    tenant = tenants.get_tenant(args.code)
    if not tenant:
        print(f"Tenant '{args.code}' not found")
        return 1
    z = clients.zabbix()
    group_name = tenant.proxy_group or f"pg-{tenant.code}"
    group_id = z.ensure_proxy_group(group_name)
    psk = secrets.token_hex(32)
    identity = f"psk-{args.name}"
    # Without --address a new proxy is reached by its name; an existing proxy keeps its address.
    z.ensure_active_proxy(args.name, proxy_groupid=group_id, psk_identity=identity, psk=psk,
                          local_address=args.address)
    print(f"Proxy '{args.name}' registered in proxy group '{group_name}'.")
    if not tenant.proxy_group:
        print(f"Set the tenant custom field msp_proxy_group = {group_name} in Nautobot so its devices use it.")
    print("\n# On the collector VM, in the edge-collector folder:")
    print("cat > .env <<'EOF'")
    print(f"ZBX_HOSTNAME={args.name}\nZBX_SERVER_HOST=<address of the Zabbix server, port 10051 reachable>\n"
          f"ZBX_TLSPSKIDENTITY={identity}\nEOF")
    print(f"echo {psk} > zabbix_proxy.psk && chmod 640 zabbix_proxy.psk")
    print("docker compose up -d")
    print("\n# Store the key in your password manager; Zabbix never shows it again.")
    return 0


def cmd_user(args) -> int:
    if args.action == "add":
        result = customers.add_user(args.code, args.login, email=args.email, full_name=args.name,
                                    grafana=args.grafana)
    else:
        result = customers.disable_user(args.code, args.login)
    for key, value in result.items():
        print(f"{key:<9} {value}")
    if args.action == "add":
        print("\nSend one-time passwords through a secure channel and ask the user to change them at first log-in.")
    return 0


def cmd_compliance(_args) -> int:
    print(compliance.run())
    return 0


def cmd_sso(args) -> int:
    from . import sso, sso_tools
    if args.action == "keys":
        # The realm's public signing key and certificate: not secret, the tools check Keycloak's signatures with them
        keys = sso.realm_keys()
        print(f"KEYCLOAK_REALM_PUBLIC_KEY={keys['public_key']}")
        print(f"KEYCLOAK_REALM_CERTIFICATE={keys['certificate']}")
        return 0
    if args.action == "sync":
        print(sso_tools.sync_semaphore_members() or "Semaphore does not sign in through Keycloak (SSO_TOOLS)")
        return 0
    password = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "")
    if not password:
        print("Set KEYCLOAK_ADMIN_PASSWORD (docker compose run -e KEYCLOAK_ADMIN_PASSWORD msp-glue mspctl sso setup)")
        return 1
    if args.action == "setup":
        for key, value in sso.setup(password).items():
            print(f"  {key:<8} {value}")
        return 0
    tools = [t.strip().lower() for value in args.tools for t in value.split(",") if t.strip()]
    if not tools:
        print(f"Name the tools, for example: mspctl sso tools grafana forgejo ({', '.join(sso.TOOL_CLIENTS)})")
        return 1
    for tool, callback in sso.setup_tools(password, tools).items():
        print(f"  {tool:<10} Keycloak client ready, signs in at {callback}")
        for key, value in sso_tools.prepare(tool).items():
            print(f"  {'':<10} {key}: {value}")
    return 0


def cmd_staff(args) -> int:
    from . import sso
    if args.action == "list":
        rows = sso.list_staff()
        print(f"{'LOG-IN':<36} {'ENABLED':<8} {'NAME':<26} GROUPS")
        for row in rows:
            print(f"{row['login']:<36} {'yes' if row['enabled'] else 'no':<8} {row['name'][:25]:<26} {row['groups']}")
        return 0
    if not args.email:
        print("Name the staff member's e-mail address, for example: mspctl staff add budi@ioh.co.id --role engineer")
        return 1
    roles = [r.strip() for value in (args.role or []) for r in value.split(",") if r.strip()]
    if args.action == "add":
        result = sso.add_staff(args.email, full_name=args.name, roles=roles or ["engineer"], otp=args.otp)
    elif args.action == "roles":
        result = sso.set_staff_roles(args.email, roles)
    elif args.action == "reset":
        result = sso.reset_staff(args.email)
    else:
        result = sso.disable_staff(args.email)
    for key, value in result.items():
        print(f"{key:<8} {value}")
    if "one-time password" in str(result.get("account", "")):
        print("\nSend the one-time password through a secure channel; Keycloak asks for a new one at first log-in.")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("msp_glue.server:app", host="0.0.0.0", port=args.port, log_level="info")
    return 0


def cmd_console(args) -> int:
    import uvicorn
    # Only Caddy reaches the console (it has no published port), so the forwarded headers it sets are trusted.
    uvicorn.run("msp_glue.console.app:app", host="0.0.0.0", port=args.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*", access_log=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mspctl", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="with 'device sync' only: show what would change in Zabbix and GLPI without writing")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check")

    b = sub.add_parser("bootstrap")
    b.add_argument("stage", choices=["tokens", "platform", "harden", "console"])
    b.add_argument("--zabbix-user", default="Admin")
    b.add_argument("--zabbix-password", default="")
    b.add_argument("--glpi-user", default="glpi")
    b.add_argument("--glpi-password", default="")
    b.add_argument("--semaphore-user", default="admin")
    b.add_argument("--semaphore-password", default="")
    b.add_argument("--forgejo-user", default="mspadmin")
    b.add_argument("--forgejo-password", default="")
    b.add_argument("--webhook-token", default="", help="GLPI token of svc-zabbix-webhook (from the tokens stage)")
    b.add_argument("--zabbix-admin-password", default="",
                   help="harden: new password for Zabbix 'Admin' (or environment NEW_ZABBIX_ADMIN_PASSWORD)")
    b.add_argument("--glpi-admin-password", default="",
                   help="harden: new password for GLPI 'glpi' (or environment NEW_GLPI_ADMIN_PASSWORD)")

    t = sub.add_parser("tenant")
    t.add_argument("action", choices=["list", "sync"])
    t.add_argument("code", nargs="?", default="")
    t.add_argument("--all", action="store_true")

    d = sub.add_parser("device")
    d.add_argument("action", choices=["sync"])
    d.add_argument("code", nargs="?", default="")

    p = sub.add_parser("proxy")
    p.add_argument("action", choices=["add"])
    p.add_argument("code")
    p.add_argument("name")
    p.add_argument("--address", default="", help="address the server and agents use to reach this proxy")

    u = sub.add_parser("user")
    u.add_argument("action", choices=["add", "disable"])
    u.add_argument("code")
    u.add_argument("login", help="log-in name, usually the user's e-mail address")
    u.add_argument("--email", default="")
    u.add_argument("--name", default="", help="full name, for example \"Budi Santoso\"")
    u.add_argument("--grafana", action="store_true", help="also create a Grafana viewer in the customer's organisation")

    sub.add_parser("compliance")

    so = sub.add_parser("sso")
    so.add_argument("action", choices=["setup", "tools", "keys", "sync"],
                    help="setup: realm, roles, console; tools <names>: sign-in to the tools; keys: the realm's "
                         "public key and certificate; sync: Semaphore project memberships now")
    so.add_argument("tools", nargs="*", help="with tools: grafana, forgejo, nautobot, semaphore, zabbix")

    st = sub.add_parser("staff")
    st.add_argument("action", choices=["add", "list", "roles", "reset", "disable"])
    st.add_argument("email", nargs="?", default="")
    st.add_argument("--name", default="", help="full name, for example \"Adam Nugraha\"")
    st.add_argument("--role", action="append",
                    help="manager, engineer, automation (includes engineer) or admin (all three); repeatable")
    st.add_argument("--otp", action="store_true", help="also require an authenticator app at first log-in")

    ch = sub.add_parser("chat")
    ch.add_argument("action", choices=["setup", "off", "test"])

    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=80)

    c = sub.add_parser("console")
    c.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.command == "bootstrap":
        # Passwords can also come from the environment (BOOTSTRAP_ZABBIX_PASSWORD and so on), which keeps
        # them off the command line: "docker compose run -e BOOTSTRAP_ZABBIX_PASSWORD msp-glue ...".
        for name in ("zabbix_password", "glpi_password", "semaphore_password", "forgejo_password", "webhook_token"):
            if not getattr(args, name):
                setattr(args, name, os.environ.get(f"BOOTSTRAP_{name.upper()}", ""))
    if args.dry_run and args.command != "device":
        parser.error("--dry-run works with 'device sync' only, for example: mspctl --dry-run device sync acme")
    _setup_logging(args.verbose)
    if args.dry_run:
        settings.dry_run = True
    handlers = {"check": cmd_check, "bootstrap": cmd_bootstrap, "tenant": cmd_tenant, "device": cmd_device,
                "proxy": cmd_proxy, "user": cmd_user, "compliance": cmd_compliance, "serve": cmd_serve,
                "sso": cmd_sso, "staff": cmd_staff, "console": cmd_console, "chat": cmd_chat}
    changes_tools = args.command in ("device", "proxy", "user", "compliance") or (
        args.command == "tenant" and args.action != "list")
    try:
        if changes_tools:
            # Same lock as the running service, so a webhook and this command never collide.
            with exclusive():
                return handlers[args.command](args)
        return handlers[args.command](args)
    except APIError as exc:
        if args.verbose:
            raise
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
