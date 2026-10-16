"""Keycloak admin REST client (tested with Keycloak 26).

Two ways to log in:
  * admin_login: the bootstrap administrator of the master realm (used once, by "mspctl sso setup")
  * service_login: msp-glue's own client in the msp realm, limited to managing that realm's users
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ..http import DEFAULT_TIMEOUT, APIError, NotFound, make_session

log = logging.getLogger("msp_glue.keycloak")


class Keycloak:
    def __init__(self, url: str, realm: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.base = url.rstrip("/")
        self.realm = realm
        self.timeout = timeout
        self.session = make_session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                     "Accept": "application/json"})

    # ------------------------------------------------------------------ log-in
    @classmethod
    def _token(cls, url: str, realm: str, data: dict) -> str:
        session = make_session()
        response = session.post(f"{url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token", data=data,
                                timeout=DEFAULT_TIMEOUT)
        if response.status_code >= 400:
            raise APIError(f"Keycloak log-in to realm {realm} failed: HTTP {response.status_code} "
                           f"{response.text[:300]}")
        return response.json()["access_token"]

    @classmethod
    def admin_login(cls, url: str, password: str, username: str = "admin", realm: str = "msp") -> "Keycloak":
        token = cls._token(url, "master", {"grant_type": "password", "client_id": "admin-cli",
                                           "username": username, "password": password})
        return cls(url, realm, token)

    @classmethod
    def service_login(cls, url: str, realm: str, client_id: str, client_secret: str) -> "Keycloak":
        token = cls._token(url, realm, {"grant_type": "client_credentials", "client_id": client_id,
                                        "client_secret": client_secret})
        return cls(url, realm, token)

    # ----------------------------------------------------------------- generic
    def request(self, method: str, path: str, payload: Any = None, params: dict | None = None) -> Any:
        """path is relative to /admin/realms/<realm>; an empty path is the realm itself."""
        url = f"{self.base}/admin/realms/{self.realm}" + (f"/{path.lstrip('/')}" if path else "")
        response = self.session.request(method, url, json=payload, params=params, timeout=self.timeout)
        if response.status_code == 404:
            raise NotFound(f"Keycloak {method} {path or self.realm}: not found")
        if response.status_code >= 400:
            raise APIError(f"Keycloak {method} {path or self.realm} failed: HTTP {response.status_code} "
                           f"{response.text[:400]}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def realm_exists(self) -> bool:
        response = self.session.get(f"{self.base}/admin/realms/{self.realm}", timeout=self.timeout)
        return response.status_code == 200

    def create_realm(self, representation: dict) -> None:
        response = self.session.post(f"{self.base}/admin/realms", json=representation, timeout=self.timeout)
        if response.status_code >= 400:
            raise APIError(f"Keycloak realm creation failed: HTTP {response.status_code} {response.text[:300]}")

    # ------------------------------------------------------------------- roles
    def ensure_role(self, name: str, description: str) -> dict:
        try:
            role = self.request("GET", f"roles/{quote(name)}")
            if role.get("description") != description:
                self.request("PUT", f"roles/{quote(name)}", {**role, "description": description})
            return role
        except NotFound:
            log.info("Keycloak: creating realm role %s", name)
            self.request("POST", "roles", {"name": name, "description": description})
            return self.request("GET", f"roles/{quote(name)}")

    # ------------------------------------------------------------------ groups
    def group_by_name(self, name: str) -> dict | None:
        for group in self.request("GET", "groups", params={"search": name, "exact": "true"}) or []:
            if group["name"] == name:
                return group
        return None

    def ensure_group(self, name: str, realm_roles: list[dict]) -> dict:
        group = self.group_by_name(name)
        if not group:
            log.info("Keycloak: creating group %s", name)
            self.request("POST", "groups", {"name": name})
            group = self.group_by_name(name)
        current = {r["name"] for r in self.request("GET", f"groups/{group['id']}/role-mappings/realm") or []}
        missing = [r for r in realm_roles if r["name"] not in current]
        if missing:
            self.request("POST", f"groups/{group['id']}/role-mappings/realm", missing)
        return group

    # ----------------------------------------------------------------- clients
    def client_by_client_id(self, client_id: str) -> dict | None:
        found = self.request("GET", "clients", params={"clientId": client_id}) or []
        return found[0] if found else None

    def ensure_client(self, representation: dict) -> dict:
        existing = self.client_by_client_id(representation["clientId"])
        if existing:
            self.request("PUT", f"clients/{existing['id']}", {**existing, **representation, "id": existing["id"]})
        else:
            log.info("Keycloak: creating client %s", representation["clientId"])
            self.request("POST", "clients", representation)
        return self.client_by_client_id(representation["clientId"])

    def ensure_protocol_mapper(self, client_uuid: str, mapper: dict) -> None:
        for existing in self.request("GET", f"clients/{client_uuid}/protocol-mappers/models") or []:
            if existing["name"] == mapper["name"]:
                self.request("PUT", f"clients/{client_uuid}/protocol-mappers/models/{existing['id']}",
                             {**existing, **mapper, "id": existing["id"]})
                return
        self.request("POST", f"clients/{client_uuid}/protocol-mappers/models", mapper)

    def grant_service_account_roles(self, client_uuid: str, source_client_id: str, role_names: list[str]) -> None:
        """Give a client's service account some client roles of another client (realm-management)."""
        user = self.request("GET", f"clients/{client_uuid}/service-account-user")
        source = self.client_by_client_id(source_client_id)
        available = {r["name"]: r for r in self.request("GET", f"clients/{source['id']}/roles") or []}
        current = {r["name"] for r in
                   self.request("GET", f"users/{user['id']}/role-mappings/clients/{source['id']}") or []}
        wanted = [available[n] for n in role_names if n in available and n not in current]
        if wanted:
            self.request("POST", f"users/{user['id']}/role-mappings/clients/{source['id']}", wanted)

    # ------------------------------------------------------------------- users
    def user_by_username(self, username: str) -> dict | None:
        for user in self.request("GET", "users", params={"username": username, "exact": "true"}) or []:
            if user["username"].lower() == username.lower():
                return user
        return None

    def user_groups(self, user_id: str) -> list[dict]:
        return self.request("GET", f"users/{user_id}/groups") or []
