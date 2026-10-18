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
import os
import secrets as pysecrets

from .clients.keycloak import Keycloak
from .http import APIError, make_session
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
# Single sign-on into the tools ("mspctl sso tools", ./scripts/install.sh sso-tools)
# =============================================================================
# tool -> where Keycloak sends the browser back, and where the tool's sign-out lands. Each tool keeps its
# own local log-in as well, for the platform's service accounts and as a way in when Keycloak is down.
TOOL_CLIENTS = {
    "grafana": {"name": "Grafana", "public": "grafana", "callback": "/login/generic_oauth", "home": "/login"},
    "forgejo": {"name": "Forgejo", "public": "forgejo", "callback": "/user/oauth2/keycloak/callback", "home": "/"},
    "nautobot": {"name": "Nautobot", "public": "nautobot", "callback": "/complete/keycloak/", "home": "/"},
    "semaphore": {"name": "Semaphore", "public": "semaphore", "callback": "/api/auth/oidc/keycloak/redirect",
                  "home": "/"},
    "zabbix": {"name": "Zabbix", "public": "zabbix", "callback": "/index_sso.php?acs", "home": "/"},
}
ROLE_CLAIM = {
    "name": "realm roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
    "config": {"multivalued": "true", "claim.name": "roles", "jsonType.label": "String",
               "id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"},
}


def tool_secret(tool: str) -> str:
    return os.environ.get(f"KEYCLOAK_{tool.upper()}_CLIENT_SECRET", "")


def setup_tools(admin_password: str, tools: list[str]) -> dict:
    """Keycloak clients for the tools, so staff sign in to them with their Keycloak account. Safe to run again."""
    unknown = [t for t in tools if t not in TOOL_CLIENTS]
    if unknown:
        raise APIError(f"no single sign-on for {', '.join(unknown)}; choose from {', '.join(TOOL_CLIENTS)}")
    kc = Keycloak.admin_login(settings.keycloak_url, admin_password, realm=settings.keycloak_realm)
    done = {}
    for tool in tools:
        spec = TOOL_CLIENTS[tool]
        base = settings.public_url(spec["public"])
        if tool == "zabbix":
            done[tool] = _saml_client(kc, base)
            continue
        secret = tool_secret(tool)
        if not secret:
            raise APIError(f"KEYCLOAK_{tool.upper()}_CLIENT_SECRET is empty (./scripts/install.sh sso-tools sets it)")
        callback = base + spec["callback"]
        client = kc.ensure_client({
            "clientId": tool, "name": spec["name"], "enabled": True, "protocol": "openid-connect",
            "publicClient": False, "secret": secret, "standardFlowEnabled": True, "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False, "frontchannelLogout": True,
            # Some tools add a trailing slash to the address they send back to; both are allowed.
            "redirectUris": sorted({callback, callback.rstrip("/"), callback.rstrip("/") + "/"}),
            "webOrigins": [base], "rootUrl": base, "baseUrl": spec["home"],
            "attributes": {"post.logout.redirect.uris": f"{base}/*"},
        })
        kc.ensure_protocol_mapper(client["id"], ROLE_CLAIM)
        if tool == "nautobot":
            # Nautobot reads the access token: it must be issued for Nautobot and name the staff groups, which
            # Nautobot's group sync turns into its own groups of the same names.
            kc.ensure_protocol_mapper(client["id"], {
                "name": "audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
                "config": {"included.client.audience": tool, "access.token.claim": "true", "id.token.claim": "false"},
            })
            kc.ensure_protocol_mapper(client["id"], {
                "name": "groups", "protocol": "openid-connect", "protocolMapper": "oidc-group-membership-mapper",
                "config": {"claim.name": "groups", "full.path": "false", "access.token.claim": "true",
                           "id.token.claim": "true", "userinfo.token.claim": "true"},
            })
        done[tool] = callback
    return done


def _saml_client(kc: Keycloak, base: str) -> str:
    """Zabbix signs staff in with SAML: Keycloak signs the assertion, Zabbix provisions the account from it."""
    acs, sls = f"{base}/index_sso.php?acs", f"{base}/index_sso.php?sls"
    client = kc.ensure_client({
        "clientId": "zabbix", "name": "Zabbix", "enabled": True, "protocol": "saml", "frontchannelLogout": True,
        "redirectUris": [acs, f"{base}/*"], "rootUrl": base, "baseUrl": "/",
        "attributes": {
            "saml.assertion.signature": "true", "saml.server.signature": "true", "saml.client.signature": "false",
            "saml.signature.algorithm": "RSA_SHA256", "saml_force_name_id_format": "true",
            "saml_name_id_format": "username", "saml.authnstatement": "true",
            "saml_assertion_consumer_url_post": acs, "saml_assertion_consumer_url_redirect": acs,
            "saml_single_logout_service_url_redirect": sls, "post.logout.redirect.uris": f"{base}/*",
        },
    })
    # Keycloak's default "role_list" scope sends one Role attribute per role, and Zabbix refuses an assertion that
    # repeats an attribute: this client sends the roles in one attribute of its own instead.
    for scope in kc.request("GET", f"clients/{client['id']}/default-client-scopes") or []:
        if scope.get("name") == "role_list":
            kc.request("DELETE", f"clients/{client['id']}/default-client-scopes/{scope['id']}")
    for name, mapper, config in (
        # One Role attribute holding every role
        ("roles", "saml-role-list-mapper", {"attribute.name": "Role", "single": "true",
                                           "attribute.nameformat": "Basic", "friendly.name": "Role"}),
        # Zabbix names the account after this attribute (the Keycloak log-in, the staff member's e-mail address)
        ("username", "saml-user-property-mapper", {"user.attribute": "username", "attribute.name": "username",
                                                  "attribute.nameformat": "Basic"}),
        ("email", "saml-user-property-mapper", {"user.attribute": "email", "attribute.name": "email",
                                               "attribute.nameformat": "Basic"}),
        ("first name", "saml-user-property-mapper", {"user.attribute": "firstName", "attribute.name": "firstName",
                                                    "attribute.nameformat": "Basic"}),
        ("last name", "saml-user-property-mapper", {"user.attribute": "lastName", "attribute.name": "lastName",
                                                   "attribute.nameformat": "Basic"}),
    ):
        kc.ensure_protocol_mapper(client["id"], {"name": name, "protocol": "saml", "protocolMapper": mapper,
                                                 "config": config})
    return acs


def realm_keys() -> dict:
    """The realm's public signing key and certificate (what Nautobot and Zabbix check signatures with)."""
    session = make_session()
    base = f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"
    realm = session.get(base, timeout=15)
    realm.raise_for_status()
    certs = session.get(f"{base}/protocol/openid-connect/certs", timeout=15)
    certs.raise_for_status()
    signing = next((k for k in certs.json().get("keys", []) if k.get("use") == "sig" and k.get("alg") == "RS256"), None)
    return {"public_key": realm.json()["public_key"], "certificate": (signing or {}).get("x5c", [""])[0]}


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
