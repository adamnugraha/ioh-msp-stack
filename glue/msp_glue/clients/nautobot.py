"""Nautobot REST and GraphQL client (tested with Nautobot 3.2)."""

from __future__ import annotations

import logging
import time
from typing import Any

from ..http import DEFAULT_TIMEOUT, APIError, NotFound, make_session

log = logging.getLogger("msp_glue.nautobot")


class Nautobot:
    def __init__(self, url: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.base = url.rstrip("/")
        self.timeout = timeout
        self.session = make_session()
        self.session.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    # --------------------------------------------------------------- generic
    def request(self, method: str, path: str, payload: Any = None, params: dict | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.base}/api/{path.lstrip('/')}"
        response = self.session.request(method, url, json=payload, params=params, timeout=self.timeout)
        if response.status_code == 404:
            raise NotFound(f"Nautobot {method} {path}: not found")
        if response.status_code >= 400:
            raise APIError(f"Nautobot {method} {path} failed: HTTP {response.status_code} {response.text[:800]}")
        return response.json() if response.content else None

    def list(self, path: str, **filters) -> list[dict]:
        params = {"limit": 500, **filters}
        results: list[dict] = []
        page = self.request("GET", path, params=params)
        while True:
            results.extend(page["results"])
            if not page.get("next"):
                return results
            page = self.request("GET", page["next"])

    def first(self, path: str, **filters) -> dict | None:
        found = self.list(path, **filters)
        return found[0] if found else None

    def create(self, path: str, payload: dict) -> dict:
        return self.request("POST", path, payload)

    def patch(self, path: str, object_id: str, payload: dict) -> dict:
        return self.request("PATCH", f"{path.rstrip('/')}/{object_id}/", payload)

    def ensure(self, path: str, lookup: dict, payload: dict) -> dict:
        existing = self.first(path, **lookup)
        if existing:
            return existing
        log.info("Nautobot: creating %s %s", path, lookup)
        return self.create(path, payload)

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        body = self.request("POST", "graphql/", {"query": query, "variables": variables or {}})
        if body.get("errors"):
            raise APIError(f"Nautobot GraphQL error: {body['errors']}")
        return _rename_custom_fields(body["data"])

    def status_id(self, name: str) -> str:
        status = self.first("extras/statuses/", name=name)
        if not status:
            raise APIError(f"Nautobot status '{name}' not found")
        return status["id"]

    def content_type(self, app_label_model: str) -> str:
        return app_label_model  # Nautobot accepts "app_label.model" strings directly

    def sync_git_repository(self, repo: dict, only_if_new: bool = False, wait_for_worker: int = 300) -> None:
        """Ask Nautobot to pull a Git repository (creating one through the API does not sync it).

        A sync runs on a Nautobot job worker. Right after start-up the worker may not be
        registered yet, and Nautobot answers 503 "No celery workers"; wait for it instead of failing.
        """
        if only_if_new and repo.get("current_head"):
            return
        deadline = time.time() + wait_for_worker
        while True:
            try:
                self.request("POST", f"extras/git-repositories/{repo['id']}/sync/")
                return
            except APIError as exc:
                if "No celery workers" not in str(exc) or time.time() > deadline:
                    raise
                log.info("Nautobot job worker not ready yet; retrying the Git sync in 10 seconds")
                time.sleep(10)

    # ------------------------------------------------------- custom fields
    def set_custom_fields(self, path: str, object_id: str, values: dict) -> None:
        self.patch(path, object_id, {"custom_fields": values})


def _rename_custom_fields(data):
    """GraphQL exposes custom fields as `_custom_field_data`; the REST API calls them `custom_fields`.
    Rename recursively so the rest of msp-glue sees one shape."""
    if isinstance(data, list):
        return [_rename_custom_fields(item) for item in data]
    if isinstance(data, dict):
        return {("custom_fields" if k == "_custom_field_data" else k): _rename_custom_fields(v) for k, v in data.items()}
    return data


DEVICES_QUERY = """
query MspDevices($tenant: [String]) {
  devices(tenant: $tenant) {
    id
    name
    serial
    asset_tag
    status { name }
    role { name }
    platform { name network_driver manufacturer { name } }
    device_type { model manufacturer { name } }
    location { name parent { name } }
    tenant { id name _custom_field_data }
    primary_ip4 { host }
    primary_ip6 { host }
    secrets_group { id name }
    _custom_field_data
  }
}
"""

TENANTS_QUERY = """
query MspTenants {
  tenants {
    id
    name
    description
    tenant_group { name }
    _custom_field_data
  }
}
"""
