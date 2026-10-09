"""Forgejo (Git server) REST client, used to create per-customer backup repositories."""

from __future__ import annotations

import logging

from ..http import DEFAULT_TIMEOUT, APIError, make_session

log = logging.getLogger("msp_glue.forgejo")


class Forgejo:
    def __init__(self, url: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.base = url.rstrip("/")
        self.timeout = timeout
        self.session = make_session()
        self.session.headers.update({"Authorization": f"token {token}", "Content-Type": "application/json"})

    def request(self, method: str, path: str, payload: dict | None = None, ok404: bool = False):
        response = self.session.request(method, f"{self.base}/api/v1/{path.lstrip('/')}", json=payload,
                                        timeout=self.timeout)
        if ok404 and response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise APIError(f"Forgejo {method} {path} failed: HTTP {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    def ensure_org(self, org: str) -> None:
        if self.request("GET", f"orgs/{org}", ok404=True) is None:
            log.info("Forgejo: creating organisation %s", org)
            self.request("POST", "orgs", {"username": org, "visibility": "private"})

    def ensure_repo(self, org: str, name: str, description: str = "") -> dict:
        repo = self.request("GET", f"repos/{org}/{name}", ok404=True)
        if repo:
            return repo
        log.info("Forgejo: creating repository %s/%s", org, name)
        return self.request("POST", f"orgs/{org}/repos", {
            "name": name, "private": True, "auto_init": True, "default_branch": "main", "description": description,
        })

    def clone_url(self, org: str, name: str) -> str:
        """Internal HTTP clone URL (Nautobot and Semaphore reach Forgejo on the platform network)."""
        return f"{self.base}/{org}/{name}.git"
