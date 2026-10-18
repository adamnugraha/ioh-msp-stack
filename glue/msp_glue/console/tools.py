"""The tools behind the console: what each one is for, its address, and how staff sign in to it.

A tool signs staff in through Keycloak once "./scripts/install.sh sso-tools" has set it up; the
installer lists those tools in SSO_TOOLS, and until then the tool keeps its own log-in.
"""

from __future__ import annotations

import os

from ..settings import settings

# tool -> (settings key for the address, what it is for, Keycloak method, label, role mapping). The mapping is
# what msp-glue sets up (sso_tools.py); GLPI keeps its own log-in (staff profiles and the customer portal).
TOOLS = {
    "Nautobot": ("nautobot", "Source of truth: customers, sites, devices, addresses, cables", "oidc",
                 "Keycloak, OpenID Connect",
                 "msp-engineer and msp-automation: view, change the inventory, run jobs; msp-manager: view"),
    "Zabbix": ("zabbix", "Monitoring, problems, availability agreements, maintenance", "saml", "Keycloak, SAML",
               "msp-engineer: Admin role in MSP NOC engineers; msp-manager: User role, read-only"),
    "GLPI": ("glpi", "Tickets, changes, service levels, assets, customer portal", "local",
             "GLPI's own log-in", "Staff profiles and the customer portal are kept in GLPI"),
    "Semaphore": ("semaphore", "Ansible playbooks per customer project", "oidc", "Keycloak, OpenID Connect",
                  "msp-automation: task runner in every customer project; msp-engineer and msp-manager: guest"),
    "Forgejo": ("forgejo", "Configuration backups, playbooks, Golden Config templates", "oidc",
                "Keycloak, OpenID Connect",
                "msp-automation: team automation (write); msp-engineer and msp-manager: teams that read"),
    "Grafana": ("grafana", "Customer dashboards", "oidc", "Keycloak, OpenID Connect",
                "msp-engineer: Editor; msp-manager: Viewer; in every organisation"),
    "Keycloak": ("keycloak", "Staff accounts, roles and single sign-on", "oidc", "Identity provider for every tool",
                 "Roles: msp-manager, msp-engineer, msp-automation"),
}


def sso_tools() -> set[str]:
    return {t.strip().lower() for t in os.environ.get("SSO_TOOLS", "").split(",") if t.strip()}


def sign_in(tool: str) -> dict:
    key, _purpose, method, label, mapping = TOOLS[tool]
    if method == "local":
        return {"method": "local", "label": label, "mapping": mapping, "enabled": False}
    if tool == "Keycloak" or key in sso_tools():
        return {"method": method, "label": label, "mapping": mapping, "enabled": True}
    return {"method": "local", "label": "The tool's own log-in (./scripts/install.sh sso-tools switches on Keycloak)",
            "mapping": "Accounts are kept in the tool itself", "enabled": False}


def url(tool: str) -> str:
    return settings.public_url(TOOLS[tool][0])


def purpose(tool: str) -> str:
    return TOOLS[tool][1]
