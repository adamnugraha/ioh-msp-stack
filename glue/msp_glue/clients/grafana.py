"""Grafana REST client (tested with Grafana 13.2 OSS and the Zabbix plugin 6.8).

Organisations can only be created by a Grafana server administrator, so this client uses
basic authentication with an admin account rather than an organisation-scoped token.
"""

from __future__ import annotations

import logging

from ..http import DEFAULT_TIMEOUT, APIError, make_session

log = logging.getLogger("msp_glue.grafana")

ZABBIX_APP = "alexanderzobnin-zabbix-app"
ZABBIX_DATASOURCE = "alexanderzobnin-zabbix-datasource"


class Grafana:
    def __init__(self, url: str, user: str, password: str, timeout: int = DEFAULT_TIMEOUT):
        self.base = url.rstrip("/")
        self.timeout = timeout
        self.session = make_session()
        self.session.auth = (user, password)
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    def request(self, method: str, path: str, payload=None, org_id: int | None = None, ok404: bool = False):
        headers = {"X-Grafana-Org-Id": str(org_id)} if org_id else {}
        response = self.session.request(method, f"{self.base}/api/{path.lstrip('/')}", json=payload,
                                        headers=headers, timeout=self.timeout)
        if ok404 and response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise APIError(f"Grafana {method} {path} failed: HTTP {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    def ensure_org(self, name: str) -> int:
        found = self.request("GET", f"orgs/name/{name}", ok404=True)
        if found:
            return int(found["id"])
        log.info("Grafana: creating organisation %s", name)
        return int(self.request("POST", "orgs", {"name": name})["orgId"])

    def ensure_admin_member(self, org_id: int, login: str = "admin") -> None:
        """The admin account must belong to the organisation to configure it."""
        users = self.request("GET", f"orgs/{org_id}/users") or []
        if not any(u.get("login") == login for u in users):
            self.request("POST", f"orgs/{org_id}/users", {"loginOrEmail": login, "role": "Admin"})

    def enable_zabbix_app(self, org_id: int) -> None:
        self.request("POST", f"plugins/{ZABBIX_APP}/settings", {"enabled": True, "pinned": True}, org_id=org_id)

    def datasource(self, org_id: int, name: str) -> dict | None:
        return self.request("GET", f"datasources/name/{name}", org_id=org_id, ok404=True)

    def create_zabbix_datasource(self, org_id: int, name: str, api_url: str, token: str) -> dict:
        payload = {
            "name": name, "type": ZABBIX_DATASOURCE, "access": "proxy", "url": api_url, "isDefault": True,
            "jsonData": {"authType": "token", "trends": True, "trendsFrom": "7d", "trendsRange": "4d",
                         "cacheTTL": "1h", "timeout": 30},
            "secureJsonData": {"apiToken": token},
        }
        return self.request("POST", "datasources", payload, org_id=org_id)["datasource"]

    def import_dashboard(self, org_id: int, dashboard: dict, folder_uid: str = "") -> dict:
        return self.request("POST", "dashboards/db", {"dashboard": dashboard, "overwrite": True,
                                                       "folderUid": folder_uid}, org_id=org_id)
