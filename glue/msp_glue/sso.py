"""Single sign-on with Keycloak: the "msp" realm, its roles, and staff accounts.

  mspctl sso setup      (once, and again after an upgrade) creates or repairs the realm, the three
                        staff roles and their groups, the MSP Console client and msp-glue's own
                        client. Needs the Keycloak bootstrap administrator password.
  mspctl staff ...      adds, lists, changes the roles of, resets and disables staff accounts with
                        msp-glue's own client.

Roles
  msp-manager      service overview and read-only operations views
  msp-engineer     operations views and safe actions (acknowledge, re-sync, compliance)
  msp-automation   start Semaphore playbooks from the console (always together with msp-engineer)
"""

from __future__ import annotations

import logging
import secrets as pysecrets

from .clients.keycloak import Keycloak
from .http import APIError
from .settings import settings

log = logging.getLogger("msp_glue.sso")

ROLES = {
    "msp-manager": "Service overview and read-only operations views in the MSP Console",
    "msp-engineer": "Operations views and safe actions in the MSP Console",
    "msp-automation": "May start Semaphore playbooks from the MSP Console",
}
GROUPS = {"MSP managers": ["msp-manager"], "MSP engineers": ["msp-engineer"], "MSP automation": ["msp-automation"]}
# What "mspctl staff add --role X" puts the account in.
ROLE_GROUPS = {
    "manager": ["MSP managers"],
    "engineer": ["MSP engineers"],
    "automation": ["MSP engineers", "MSP automation"],
    "admin": ["MSP managers", "MSP engineers", "MSP automation"],
}
CONSOLE_CLIENT = "msp-console"
GLUE_CLIENT = "msp-glue"


def console_url() -> str:
    return settings.public_url("console")


# =============================================================================
# Set-up
# =============================================================================
def setup(admin_password: str) -> dict:
    """Create or repair the realm and its clients. Safe to run again."""
    if not settings.keycloak_console_secret or not settings.keycloak_glue_secret:
        raise APIError("KEYCLOAK_CONSOLE_CLIENT_SECRET and KEYCLOAK_GLUE_CLIENT_SECRET must be set in .env "
                       "(./scripts/install.sh sso sets them)")
    kc = Keycloak.admin_login(settings.keycloak_url, admin_password, realm=settings.keycloak_realm)
    realm = {
        "realm": settings.keycloak_realm, "enabled": True, "displayName": "IOH Managed Services",
        "loginWithEmailAllowed": True, "duplicateEmailsAllowed": False, "registrationAllowed": False,
        # No mail server is configured for the realm yet, so self-service password reset stays off:
        # an administrator resets passwords with "mspctl staff reset".
        "resetPasswordAllowed": False, "rememberMe": False, "verifyEmail": False,
        "bruteForceProtected": True, "failureFactor": 10, "permanentLockout": False,
        "sslRequired": "external", "passwordPolicy": "length(12) and notUsername and notEmail",
        "ssoSessionIdleTimeout": 3600, "ssoSessionMaxLifespan": 36000, "accessTokenLifespan": 300,
    }
    if not kc.realm_exists():
        log.info("Keycloak: creating realm %s", settings.keycloak_realm)
        kc.create_realm(realm)
    else:
        current = kc.request("GET", "")
        kc.request("PUT", "", {**current, **realm})

    roles = {name: kc.ensure_role(name, description) for name, description in ROLES.items()}
    for group, role_names in GROUPS.items():
        kc.ensure_group(group, [roles[n] for n in role_names])

    base = console_url()
    console = kc.ensure_client({
        "clientId": CONSOLE_CLIENT, "name": "MSP Console", "enabled": True, "protocol": "openid-connect",
        "publicClient": False, "secret": settings.keycloak_console_secret,
        "standardFlowEnabled": True, "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False, "frontchannelLogout": True,
        "redirectUris": [f"{base}/auth/callback"], "webOrigins": [base], "rootUrl": base, "baseUrl": "/",
        "attributes": {"post.logout.redirect.uris": f"{base}/*", "pkce.code.challenge.method": "S256"},
    })
    kc.ensure_protocol_mapper(console["id"], {
        "name": "realm roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
        "config": {"multivalued": "true", "claim.name": "roles", "jsonType.label": "String",
                   "id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"},
    })

    glue = kc.ensure_client({
        "clientId": GLUE_CLIENT, "name": "msp-glue staff management", "enabled": True,
        "protocol": "openid-connect", "publicClient": False, "secret": settings.keycloak_glue_secret,
        "standardFlowEnabled": False, "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": True,
    })
    kc.grant_service_account_roles(glue["id"], "realm-management",
                                   ["manage-users", "view-users", "query-users", "query-groups", "view-realm"])
    log.info("Keycloak realm %s ready: console at %s", settings.keycloak_realm, base)
    return {"realm": settings.keycloak_realm, "console": base, "issuer": f"{settings.public_url('keycloak')}/realms/"
            f"{settings.keycloak_realm}"}


# =============================================================================
# Staff accounts
# =============================================================================
def _kc() -> Keycloak:
    if not settings.keycloak_glue_secret:
        raise APIError("KEYCLOAK_GLUE_CLIENT_SECRET is empty: run ./scripts/install.sh sso first")
    return Keycloak.service_login(settings.keycloak_url, settings.keycloak_realm, GLUE_CLIENT,
                                  settings.keycloak_glue_secret)


def _one_time_password() -> str:
    # Meets the realm policy (12 characters or more); the user must replace it at first log-in.
    return pysecrets.token_urlsafe(12) + "aA1!"


def _set_groups(kc: Keycloak, user_id: str, roles: list[str]) -> list[str]:
    wanted = sorted({g for role in roles for g in ROLE_GROUPS[role]})
    current = {g["name"]: g["id"] for g in kc.user_groups(user_id)}
    for name in wanted:
        if name not in current:
            group = kc.group_by_name(name)
            if not group:
                raise APIError(f"Keycloak group '{name}' missing: run ./scripts/install.sh sso")
            kc.request("PUT", f"users/{user_id}/groups/{group['id']}")
    return wanted


def add_staff(email: str, full_name: str = "", roles: list[str] | None = None, otp: bool = False) -> dict:
    roles = roles or ["engineer"]
    unknown = [r for r in roles if r not in ROLE_GROUPS]
    if unknown:
        raise APIError(f"unknown role {', '.join(unknown)}: use {', '.join(ROLE_GROUPS)}")
    kc = _kc()
    email = email.strip().lower()
    first, _, last = (full_name or email.split("@")[0]).partition(" ")
    user = kc.user_by_username(email)
    result: dict = {"login": email}
    if user:
        changes = {"enabled": True}
        if full_name:   # keep the stored name unless a new one is given
            changes.update({"firstName": first, "lastName": last})
        kc.request("PUT", f"users/{user['id']}", {**user, **changes})
        result["account"] = "existing account, enabled; password unchanged"
    else:
        actions = ["UPDATE_PASSWORD"] + (["CONFIGURE_TOTP"] if otp else [])
        kc.request("POST", "users", {"username": email, "email": email, "emailVerified": True, "enabled": True,
                                     "firstName": first, "lastName": last, "requiredActions": actions})
        user = kc.user_by_username(email)
        password = _one_time_password()
        kc.request("PUT", f"users/{user['id']}/reset-password",
                   {"type": "password", "value": password, "temporary": True})
        result["account"] = f"created, one-time password: {password}"
    result["groups"] = ", ".join(_set_groups(kc, user["id"], roles))
    return result


def set_staff_roles(email: str, roles: list[str]) -> dict:
    """Give an account exactly these console roles: missing groups are added, other MSP groups removed."""
    unknown = [r for r in roles if r not in ROLE_GROUPS]
    if unknown or not roles:
        raise APIError(f"name the roles with --role: {', '.join(ROLE_GROUPS)}")
    kc = _kc()
    user = kc.user_by_username(email.strip().lower())
    if not user:
        raise APIError(f"no staff account {email}")
    wanted = {g for role in roles for g in ROLE_GROUPS[role]}
    for group in kc.user_groups(user["id"]):
        if group["name"] in GROUPS and group["name"] not in wanted:
            kc.request("DELETE", f"users/{user['id']}/groups/{group['id']}")
    groups = _set_groups(kc, user["id"], roles)
    return {"login": user["username"], "groups": ", ".join(groups),
            "account": "roles changed; open console sessions follow within five minutes"}


def reset_staff(email: str) -> dict:
    kc = _kc()
    user = kc.user_by_username(email.strip().lower())
    if not user:
        raise APIError(f"no staff account {email}")
    password = _one_time_password()
    kc.request("PUT", f"users/{user['id']}/reset-password", {"type": "password", "value": password, "temporary": True})
    kc.request("POST", f"users/{user['id']}/logout")
    return {"login": user["username"], "account": f"new one-time password: {password}"}


def disable_staff(email: str) -> dict:
    kc = _kc()
    user = kc.user_by_username(email.strip().lower())
    if not user:
        return {"login": email, "account": "not found"}
    kc.request("PUT", f"users/{user['id']}", {**user, "enabled": False})
    kc.request("POST", f"users/{user['id']}/logout")   # ends the user's open sessions now
    return {"login": user["username"], "account": "disabled, open sessions ended"}


def list_staff() -> list[dict]:
    kc = _kc()
    users = kc.request("GET", "users", params={"max": 500, "briefRepresentation": "true"}) or []
    rows = []
    for user in sorted(users, key=lambda u: u["username"]):
        if user["username"].startswith("service-account-"):
            continue
        rows.append({"login": user["username"], "name": f"{user.get('firstName', '')} {user.get('lastName', '')}".strip(),
                     "enabled": user.get("enabled", False),
                     "groups": ", ".join(sorted(g["name"] for g in kc.user_groups(user["id"])))})
    return rows
