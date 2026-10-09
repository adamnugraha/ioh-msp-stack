"""Semaphore UI REST client (tested with Semaphore 2.19, Community edition)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..http import DEFAULT_TIMEOUT, APIError, make_session

log = logging.getLogger("msp_glue.semaphore")


class Semaphore:
    def __init__(self, url: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.base = url.rstrip("/") + "/api"
        self.timeout = timeout
        self.session = make_session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                     "Accept": "application/json"})

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        response = self.session.request(method, f"{self.base}/{path.lstrip('/')}", json=payload, timeout=self.timeout)
        if response.status_code >= 400:
            raise APIError(f"Semaphore {method} {path} failed: HTTP {response.status_code} {response.text[:500]}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    @classmethod
    def login(cls, url: str, username: str, password: str) -> "Semaphore":
        """Log in with a password and create an API token (used once by the bootstrap)."""
        session = make_session()
        base = url.rstrip("/") + "/api"
        response = session.post(f"{base}/auth/login", json={"auth": username, "password": password}, timeout=DEFAULT_TIMEOUT)
        if response.status_code >= 400:
            raise APIError(f"Semaphore login failed: HTTP {response.status_code} {response.text[:200]}")
        token = session.post(f"{base}/user/tokens", timeout=DEFAULT_TIMEOUT)
        if token.status_code >= 400:
            raise APIError(f"Semaphore token creation failed: HTTP {token.status_code} {token.text[:200]}")
        return cls(url, token.json()["id"])

    # ------------------------------------------------------------- projects
    def projects(self) -> list[dict]:
        return self.request("GET", "projects") or []

    def ensure_project(self, name: str, max_parallel_tasks: int = 2) -> int:
        for project in self.projects():
            if project["name"] == name:
                return project["id"]
        log.info("Semaphore: creating project %s", name)
        return self.request("POST", "projects", {"name": name, "max_parallel_tasks": max_parallel_tasks})["id"]

    def _ensure_named(self, project_id: int, kind: str, name: str, payload: dict, update: bool = True) -> int:
        existing = [o for o in (self.request("GET", f"project/{project_id}/{kind}") or []) if o.get("name") == name]
        body = {"name": name, "project_id": project_id, **payload}
        if existing:
            object_id = existing[0]["id"]
            if update:
                self.request("PUT", f"project/{project_id}/{kind}/{object_id}", {"id": object_id, **body})
            return object_id
        log.info("Semaphore: creating %s '%s' in project %s", kind, name, project_id)
        return self.request("POST", f"project/{project_id}/{kind}", body)["id"]

    def ensure_key(self, project_id: int, name: str, key_type: str = "none", login: str = "", secret: str = "",
                   private_key: str = "") -> int:
        payload: dict[str, Any] = {"type": key_type}
        if key_type == "login_password":
            payload["login_password"] = {"login": login, "password": secret}
        elif key_type == "ssh":
            payload["ssh"] = {"login": login, "private_key": private_key, "passphrase": ""}
        # Keys are write-only in Semaphore; do not overwrite an existing key on every run.
        return self._ensure_named(project_id, "keys", name, payload, update=False)

    def ensure_repository(self, project_id: int, name: str, git_url: str, branch: str, key_id: int) -> int:
        return self._ensure_named(project_id, "repositories", name,
                                  {"git_url": git_url, "git_branch": branch, "ssh_key_id": key_id})

    def ensure_inventory(self, project_id: int, name: str, content: str, ssh_key_id: int,
                         become_key_id: int | None = None) -> int:
        payload: dict[str, Any] = {"inventory": content, "type": "static-yaml", "ssh_key_id": ssh_key_id}
        if become_key_id:
            payload["become_key_id"] = become_key_id
        return self._ensure_named(project_id, "inventory", name, payload)

    def ensure_environment(self, project_id: int, name: str, extra_vars: dict | None = None,
                           env: dict | None = None) -> int:
        return self._ensure_named(project_id, "environment", name,
                                  {"json": json.dumps(extra_vars or {}), "env": json.dumps(env or {})})

    def ensure_template(self, project_id: int, name: str, playbook: str, inventory_id: int, repository_id: int,
                        environment_id: int, description: str = "", skip_galaxy_install: bool = False) -> int:
        return self._ensure_named(project_id, "templates", name, {
            "playbook": playbook, "inventory_id": inventory_id, "repository_id": repository_id,
            "environment_id": environment_id, "app": "ansible", "arguments": "[]",
            "allow_override_args_in_task": True, "description": description,
            # Without allow_override_limit Semaphore silently ignores the host limit sent with a task
            # and runs against the whole inventory. Keep it on.
            "task_params": {"allow_override_limit": True, "allow_debug": True,
                            "skip_galaxy_install": skip_galaxy_install,
                            # The template decides; a task that does not send the flag would otherwise
                            # switch the Galaxy install back on (Semaphore 2.19 behaviour).
                            "allow_override_skip_galaxy_install": False},
        })

    def templates(self, project_id: int) -> list[dict]:
        return self.request("GET", f"project/{project_id}/templates") or []

    # ---------------------------------------------------------------- tasks
    def run_task(self, project_id: int, template_id: int, extra_vars: dict | None = None, limit: str = "",
                 message: str = "") -> dict:
        payload: dict[str, Any] = {"template_id": template_id, "params": {}}
        if extra_vars:
            payload["environment"] = json.dumps(extra_vars)
        if limit:
            payload["params"]["limit"] = [h.strip() for h in limit.split(",") if h.strip()]
        if message:
            payload["message"] = message
        return self.request("POST", f"project/{project_id}/tasks", payload)

    def task(self, project_id: int, task_id: int) -> dict:
        return self.request("GET", f"project/{project_id}/tasks/{task_id}")

    def task_output(self, project_id: int, task_id: int) -> str:
        lines = self.request("GET", f"project/{project_id}/tasks/{task_id}/output") or []
        return "\n".join(line.get("output", "") for line in lines)

    def wait(self, project_id: int, task_id: int, timeout: int = 1800, poll: int = 5) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.task(project_id, task_id)
            if task.get("status") in ("success", "error", "stopped"):
                return task
            time.sleep(poll)
        raise APIError(f"Semaphore task {task_id} did not finish within {timeout}s")

    def final_output(self, project_id: int, task_id: int, settle: float = 2.0, tries: int = 6) -> str:
        """Task output after completion. Semaphore can still be writing the last lines when the
        status changes, so read until the line count stops growing."""
        previous = -1
        output = ""
        for _ in range(tries):
            output = self.task_output(project_id, task_id)
            count = len(output.splitlines())
            if count == previous:
                break
            previous = count
            time.sleep(settle)
        return output
