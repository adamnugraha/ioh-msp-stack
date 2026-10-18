"""The tools' side of single sign-on: what each tool needs so staff can sign in with their Keycloak account.

The Keycloak roles decide what someone may do in each tool:

  tool        msp-manager             msp-engineer                         msp-automation (with engineer)
  Grafana     Viewer, every org       Editor, every org                    Editor, every org
  Forgejo     team managers (read)    team engineers (read)                team automation (write)
  Nautobot    view everything         view, change the inventory, jobs    as engineer
  Semaphore   guest, every project    guest, every project                 task runner, every project
  Zabbix      User role, read-only    Admin role, MSP NOC engineers        as engineer

Grafana maps the roles at every sign-in by itself (its settings in components/grafana/compose.yaml). Forgejo
and Nautobot get their teams, groups and permissions here, and Zabbix its SAML user directory; Semaphore's
project memberships follow the roles at every reconcile (Semaphore has no role mapping of its own).
Every tool keeps its local log-in for the service accounts and as the way in when Keycloak is down.
"""

from __future__ import annotations

import json
import logging
import os

from . import clients
from .clients.zabbix import PERM_READ
from .http import APIError
from .settings import settings

log = logging.getLogger("msp_glue.sso_tools")

ENGINEER, MANAGER, AUTOMATION = "msp-engineer", "msp-manager", "msp-automation"


def enabled(tool: str) -> bool:
    """Tools that sign staff in through Keycloak: SSO_TOOLS in .env (./scripts/install.sh sso-tools sets it)."""
    return tool in {t.strip().lower() for t in os.environ.get("SSO_TOOLS", "").split(",") if t.strip()}


def prepare(tool: str) -> dict:
    return {"forgejo": forgejo_teams, "nautobot": nautobot_groups, "zabbix": zabbix_saml,
            "semaphore": lambda: {"projects": "staff join every customer project at their next reconcile"},
            "grafana": lambda: {"roles": "mapped at every sign-in"}}[tool]()


# =============================================================================
# Forgejo: one team per role in the platform's organisation
# =============================================================================
FORGEJO_TEAMS = {
    "engineers": (ENGINEER, "read", "Engineers: read the backups, playbooks and templates"),
    "managers": (MANAGER, "read", "Managers: read the backups, playbooks and templates"),
    "automation": (AUTOMATION, "write", "Automation: change playbooks and Golden Config templates"),
}
FORGEJO_UNITS = ["repo.code", "repo.issues", "repo.pulls", "repo.releases", "repo.wiki"]


def forgejo_teams() -> dict:
    fj = clients.forgejo()
    org = settings.forgejo_org
    existing = {t["name"]: t for t in fj.request("GET", f"orgs/{org}/teams?limit=50") or []}
    for name, (_role, access, description) in FORGEJO_TEAMS.items():
        body = {"name": name, "description": description, "permission": access, "includes_all_repositories": True,
                "can_create_org_repo": False, "units": FORGEJO_UNITS,
                "units_map": {unit: access for unit in FORGEJO_UNITS}}
        if name in existing:
            fj.request("PATCH", f"teams/{existing[name]['id']}", body)
        else:
            fj.request("POST", f"orgs/{org}/teams", body)
    return {"group_team_map": forgejo_team_map()}


def forgejo_team_map() -> str:
    """What Forgejo's Keycloak sign-in source puts people in, from the roles claim (--group-team-map)."""
    org = settings.forgejo_org
    return json.dumps({role: {org: [name]} for name, (role, _access, _d) in FORGEJO_TEAMS.items()},
                      separators=(",", ":"))


# =============================================================================
# Nautobot: three groups and their object permissions
# =============================================================================
# The Keycloak groups (sso.GROUPS): Nautobot's own group sync gives staff the groups of the same names.
NAUTOBOT_GROUPS = ["MSP managers", "MSP engineers", "MSP automation"]
# Apps whose objects nobody signing in through Keycloak needs to see (accounts, sessions, internals)
_HIDDEN_APPS = {"users", "auth", "admin", "contenttypes", "sessions", "social_django", "django_celery_beat",
                "django_celery_results", "constance", "silk", "taggit", "health_check", "db"}
# The inventory engineers keep: devices, addresses, cables, circuits, customers, Golden Config data
_INVENTORY_APPS = {"dcim", "ipam", "circuits", "tenancy", "virtualization", "cloud", "wireless", "vpn",
                   "nautobot_golden_config"}
_INVENTORY_EXTRAS = {"extras.tag", "extras.note", "extras.contact", "extras.contactassociation", "extras.team"}


def nautobot_groups() -> dict:
    nb = clients.nautobot()
    types = sorted(f"{c['app_label']}.{c['model']}" for c in nb.list("extras/content-types/"))
    view = [t for t in types if t.split(".")[0] not in _HIDDEN_APPS]
    change = [t for t in types if t.split(".")[0] in _INVENTORY_APPS or t in _INVENTORY_EXTRAS]
    groups = {name: nb.ensure("users/groups/", {"name": name}, {"name": name})["id"] for name in NAUTOBOT_GROUPS}
    wanted = {
        "MSP staff: view everything": (["view"], view, list(groups.values()),
                                       "Staff signing in through Keycloak: read every object"),
        "MSP engineers: change the inventory": (["add", "change", "delete"], change,
                                                [groups["MSP engineers"], groups["MSP automation"]],
                                                "Devices, addresses, cables, circuits, customers, Golden Config"),
        "MSP engineers: run jobs": (["run"], ["extras.job"], [groups["MSP engineers"], groups["MSP automation"]],
                                    "Golden Config backups, intended configuration and compliance"),
    }
    for name, (actions, object_types, group_ids, description) in wanted.items():
        body = {"name": name, "description": description, "enabled": True, "actions": actions,
                "object_types": object_types, "groups": group_ids}
        found = nb.first("users/permissions/", name=name)
        if found:
            nb.patch("users/permissions/", found["id"], body)
        else:
            nb.create("users/permissions/", body)
    return {"groups": ", ".join(groups), "view": f"{len(view)} object types", "change": f"{len(change)} object types"}


# =============================================================================
# Semaphore: project memberships from the Keycloak roles
# =============================================================================
def semaphore_role(roles: set[str]) -> str | None:
    if AUTOMATION in roles and ENGINEER in roles:
        return "task_runner"
    if ENGINEER in roles or MANAGER in roles:
        return "guest"
    return None


def staff_roles() -> dict[str, set[str]]:
    """Log-in (e-mail address) -> realm roles, for every enabled staff account in Keycloak."""
    from .sso import GROUPS, _kc
    kc = _kc()
    out: dict[str, set[str]] = {}
    for user in kc.request("GET", "users", params={"max": 1000, "briefRepresentation": "true"}) or []:
        if user["username"].startswith("service-account-") or not user.get("enabled"):
            continue
        roles = {r for g in kc.user_groups(user["id"]) for r in GROUPS.get(g["name"], [])}
        out[user["username"].lower()] = roles
        if user.get("email"):
            out[user["email"].lower()] = roles
    return out


def sync_semaphore_members() -> dict:
    """Staff who signed in to Semaphore through Keycloak join every customer project with the role their Keycloak
    roles give, and leave them when the roles go. Local Semaphore accounts (admin, the API user) are left alone."""
    if not enabled("semaphore"):
        return {}
    from .tenants import managed_tenants
    roles = staff_roles()
    sem = clients.semaphore()
    users = [u for u in sem.request("GET", "users") or [] if u.get("external")]
    projects = [int(t.semaphore_project_id) for t in managed_tenants() if t.valid and t.semaphore_project_id]
    changed = 0
    for project in projects:
        members = {m["id"]: m for m in sem.request("GET", f"project/{project}/users") or []}
        for user in users:
            login = (user.get("username") or "").lower()
            want = semaphore_role(roles.get(login) or roles.get((user.get("email") or "").lower()) or set())
            have = (members.get(user["id"]) or {}).get("role")
            if want == have:
                continue
            if want and not have:
                sem.request("POST", f"project/{project}/users", {"user_id": user["id"], "role": want})
            elif want:
                sem.request("PUT", f"project/{project}/users/{user['id']}", {"role": want})
            else:
                sem.request("DELETE", f"project/{project}/users/{user['id']}")
            changed += 1
    if changed:
        log.info("Semaphore: %s project memberships follow the Keycloak roles", changed)
    return {"changed": changed, "projects": len(projects), "staff": len(users)}


# =============================================================================
# Zabbix: SAML with just-in-time provisioning
# =============================================================================
def zabbix_saml() -> dict:
    z = clients.zabbix()
    zm = settings.mappings["zabbix"]
    idp = f"{settings.public_url('keycloak')}/realms/{settings.keycloak_realm}"

    def one(method, name, key, field="name"):
        found = z.call(method, {"filter": {field: [name]}, "output": [key]})
        if not found:
            raise APIError(f"Zabbix has no {method.split('.')[0]} '{name}'")
        return found[0][key]

    every = z.hostgroups_with_prefix(zm["customer_group_root"])
    managers = z.ensure_usergroup(zm.get("manager_usergroup", "MSP managers"), [(g["groupid"], PERM_READ) for g in every])
    noc = one("usergroup.get", zm["noc_usergroup"], "usrgrpid")
    disabled = one("usergroup.get", "Disabled", "usrgrpid")
    admin_role, user_role = one("role.get", "Admin role", "roleid"), one("role.get", "User role", "roleid")
    directory = {
        "idp_type": 2, "name": "Keycloak", "description": "Staff sign-in (realm msp). Managed by msp-glue.",
        "idp_entityid": idp, "sso_url": f"{idp}/protocol/saml", "slo_url": f"{idp}/protocol/saml",
        "sp_entityid": "zabbix", "nameid_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
        "username_attribute": "username", "sign_assertions": 1, "sign_messages": 0, "sign_authn_requests": 0,
        "sign_logout_requests": 0, "sign_logout_responses": 0, "encrypt_nameid": 0, "encrypt_assertions": 0,
        "group_name": "Role", "user_username": "firstName", "user_lastname": "lastName", "provision_status": 1,
        "provision_groups": [
            {"name": ENGINEER, "roleid": admin_role, "user_groups": [{"usrgrpid": noc}]},
            {"name": MANAGER, "roleid": user_role, "user_groups": [{"usrgrpid": managers}]},
        ],
    }
    found = z.call("userdirectory.get", {"output": ["userdirectoryid"], "filter": {"idp_type": 2}})
    if found:
        z.call("userdirectory.update", {"userdirectoryid": found[0]["userdirectoryid"], **directory})
    else:
        z.call("userdirectory.create", directory)
    z.call("authentication.update", {"saml_auth_enabled": 1, "saml_jit_status": 1, "saml_case_sensitive": 0,
                                     "jit_provision_interval": "1h", "disabled_usrgrpid": disabled})
    return {"identity provider": idp, "engineers": zm["noc_usergroup"], "managers": zm.get("manager_usergroup", "MSP managers")}
