"""Customer portal accounts: a GLPI self-service user and, in phase 3, a Grafana viewer.

Both are limited to one customer: the GLPI account holds the "Self-Service" profile on the
customer's entity only (never the root entity), and the Grafana account is a Viewer in the
customer's own organisation, whose Zabbix data source can only read that customer's hosts.
"""

from __future__ import annotations

import logging
import secrets as pysecrets

from . import clients, dashboards
from .clients.grafana import Grafana
from .common import Tenant
from .http import APIError
from .settings import settings
from .tenants import get_tenant

log = logging.getLogger("msp_glue.customers")

SELF_SERVICE = "Self-Service"


def _password() -> str:
    # Long, random, and meets GLPI and Grafana password rules (letters, digit, symbol).
    return pysecrets.token_urlsafe(14) + "aA1!"


def _tenant(code: str) -> Tenant:
    tenant = get_tenant(code)
    if not tenant or not tenant.glpi_entity_id:
        raise APIError(f"Customer '{code}' not found or not provisioned yet (run: mspctl tenant sync {code})")
    return tenant


def add_user(code: str, login: str, email: str = "", full_name: str = "", grafana: bool = False) -> dict:
    tenant = _tenant(code)
    email = email or (login if "@" in login else "")
    first, _, last = (full_name or login).partition(" ")
    result: dict = {"customer": tenant.code, "login": login}
    entity = int(tenant.glpi_entity_id)

    with clients.glpi() as g:
        profile = g.find("Profile", name=SELF_SERVICE)
        if not profile:
            raise APIError("GLPI profile 'Self-Service' not found")
        profile_id = int(profile[0]["id"])
        users = g.find("User", name=login)
        if users:
            uid = int(users[0]["id"])
            g.update("User", uid, {"is_active": 1})
            result["glpi"] = "existing account re-activated, password unchanged"
        else:
            password = _password()
            # GLPI only hashes a password sent together with an identical "password2"; without it the
            # value is stored as given and log-in fails with "your password has expired".
            uid = g.add("User", {"name": login, "firstname": first, "realname": last, "is_active": 1,
                                 "password": password, "password2": password,
                                 "entities_id": entity, "profiles_id": profile_id})
            result["glpi"] = f"created, one-time password: {password}"
        if email and not g.find("UserEmail", users_id=uid, email=email):
            g.add("UserEmail", {"users_id": uid, "email": email, "is_default": 1})
        # Exactly one authorisation: Self-Service on this customer's entity, not recursive.
        # GLPI can add a default profile on the active (root) entity when a user is created; remove it.
        keep = None
        for link in g.find("Profile_User", users_id=uid):
            if int(link["entities_id"]) == entity and int(link["profiles_id"]) == profile_id and keep is None:
                keep = int(link["id"])
                if int(link.get("is_recursive") or 0):
                    g.update("Profile_User", keep, {"is_recursive": 0})
            else:
                g.request("DELETE", f"Profile_User/{link['id']}", {"input": {"id": int(link["id"])}, "force_purge": True})
        if keep is None:
            g.add("Profile_User", {"users_id": uid, "profiles_id": profile_id, "entities_id": entity, "is_recursive": 0})
        g.update("User", uid, {"profiles_id": profile_id, "entities_id": entity})
        group = g.find("Group", name=f"{tenant.name} requesters", entities_id=entity)
        if group and not g.find("Group_User", users_id=uid, groups_id=int(group[0]["id"])):
            g.add("Group_User", {"users_id": uid, "groups_id": int(group[0]["id"])})

    if grafana:
        if not dashboards.enabled():
            result["grafana"] = "skipped: Grafana is not configured (GRAFANA_URL)"
        else:
            result["grafana"] = _grafana_viewer(tenant, login, email, full_name or login)
    log.info("Customer user %s for %s: %s", login, tenant.code, {k: v for k, v in result.items() if "password" not in str(v)})
    return result


def _grafana_viewer(tenant: Tenant, login: str, email: str, name: str) -> str:
    gf = Grafana(settings.grafana_url, settings.grafana_admin_user, settings.grafana_admin_password)
    org_id = gf.ensure_org(tenant.name)
    user = gf.request("GET", f"users/lookup?loginOrEmail={login}", ok404=True)
    message = "existing account"
    if not user:
        password = _password()
        created = gf.request("POST", "admin/users", {"name": name, "email": email or f"{login}@invalid.local",
                                                     "login": login, "password": password, "OrgId": org_id})
        user = {"id": created["id"]}
        message = f"created, one-time password: {password}"
    uid = int(user["id"])
    members = gf.request("GET", f"orgs/{org_id}/users") or []
    if any(int(m["userId"]) == uid for m in members):
        gf.request("PATCH", f"orgs/{org_id}/users/{uid}", {"role": "Viewer"})
    else:
        gf.request("POST", f"orgs/{org_id}/users", {"loginOrEmail": login, "role": "Viewer"})
    # A customer account belongs to its own organisation only: remove it from every other one, such as
    # the "Main Org." where Grafana places new accounts (components/grafana/compose.yaml).
    for org in gf.request("GET", f"users/{uid}/orgs") or []:
        if int(org["orgId"]) != org_id:
            try:
                gf.request("DELETE", f"orgs/{org['orgId']}/users/{uid}")
            except APIError as exc:
                log.warning("Grafana: could not remove %s from organisation %s: %s", login, org["name"], exc)
    return message + f", Viewer in organisation {org_id}"


def disable_user(code: str, login: str) -> dict:
    tenant = _tenant(code)
    result: dict = {"customer": tenant.code, "login": login}
    with clients.glpi() as g:
        users = g.find("User", name=login)
        if users:
            g.update("User", int(users[0]["id"]), {"is_active": 0})
            result["glpi"] = "deactivated"
        else:
            result["glpi"] = "not found"
    if dashboards.enabled():
        gf = Grafana(settings.grafana_url, settings.grafana_admin_user, settings.grafana_admin_password)
        user = gf.request("GET", f"users/lookup?loginOrEmail={login}", ok404=True)
        if user:
            gf.request("POST", f"admin/users/{user['id']}/disable")
            result["grafana"] = "disabled"
        else:
            result["grafana"] = "not found"
    return result
