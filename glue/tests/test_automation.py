"""The paths that run playbooks on customer devices, and the webhooks that start them (no real tools needed).

Run:  cd glue && python -m pytest -q
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
os.environ.setdefault("GLUE_MAPPINGS", str(HERE.parent / "mappings.yaml"))
os.environ.setdefault("CONSOLE_DATA_DIR", tempfile.mkdtemp(prefix="console-test-"))
os.environ.setdefault("CONSOLE_SESSION_SECRET", "test-secret-test-secret-test-secret")
os.environ.setdefault("MSP_DOMAIN", "msp.test.internal")

from fastapi.testclient import TestClient  # noqa: E402

from msp_glue import changes, server  # noqa: E402
from msp_glue.clients.semaphore import Semaphore  # noqa: E402
from msp_glue.common import Tenant  # noqa: E402
from msp_glue.console import actions, auth  # noqa: E402
from msp_glue.settings import settings  # noqa: E402

BOUNCE = {"id": 3, "name": "Bounce interface", "task_params": {"allow_override_limit": True}}


def tenant() -> Tenant:
    return Tenant(id="t1", name="PT ACME Retail Indonesia", code="acme", glpi_entity_id=2, semaphore_project_id=1)


class FakeSemaphore:
    def __init__(self):
        self.runs = []

    def templates(self, project):
        return [BOUNCE]

    def run_task(self, project, template_id, extra_vars=None, limit="", message=""):
        self.runs.append({"template": template_id, "extra": extra_vars, "limit": limit})
        return {"id": 11}


class FakeGLPI:
    """One change or ticket of the customer (entity 2), its follow-ups, and the notes added to it."""

    def __init__(self, status: int, followups: list | None = None):
        self.status, self.followups, self.notes = status, followups or [], []

    def get(self, itemtype, item_id, expand_dropdowns=None, **_):
        if itemtype == "Entity":
            return {"id": item_id, "entities_id": 1 if item_id == 2 else 0}
        if expand_dropdowns:
            return {"name": "Bounce Gi0/2", "global_validation": 0,
                    "itilcategories_id": "Managed infrastructure &gt; Standard change &gt; Bounce interface",
                    "content": "<p>automation:<br>device: rtr01<br>interface: Gi0/2<br>"
                               "msp_console_user: boss@ioh.example</p>"}
        return {"status": self.status, "entities_id": 2, "is_deleted": 0, "name": "Bounce Gi0/2"}

    def request(self, method, path, *args, **kwargs):
        return self.followups

    def add_followup(self, itemtype, item_id, text, **_):
        self.notes.append(text)


@pytest.fixture
def tools(monkeypatch):
    def install(glpi: FakeGLPI) -> FakeSemaphore:
        sem = FakeSemaphore()

        @contextmanager
        def opened():
            yield glpi
        for module in (changes, actions):
            monkeypatch.setattr(module.clients, "glpi", opened)
            monkeypatch.setattr(module.clients, "semaphore", lambda: sem)
        monkeypatch.setattr(changes, "managed_tenants", lambda: [tenant()])
        monkeypatch.setattr(changes, "_report_result", lambda *args: None)
        return sem
    return install


# ------------------------------------------------------------------- GLPI change -> playbook
def test_accepted_change_runs_once_on_its_device(tools):
    glpi = FakeGLPI(status=7)
    sem = tools(glpi)
    assert changes.handle_change(4) == "started task 11"
    assert sem.runs == [{"template": 3, "limit": "rtr01",
                         "extra": {"glpi_change_id": 4, "device": "rtr01", "interface": "Gi0/2"}}]
    assert "msp_console_user" in glpi.notes[0]        # named as ignored in the follow-up

    glpi.followups = [{"content": glpi.notes[0]}]       # the next webhook for the same change
    assert changes.handle_change(4) == "ignored: already automated"
    assert len(sem.runs) == 1


def test_change_that_is_not_accepted_runs_nothing(tools):
    sem = tools(FakeGLPI(status=10))
    assert changes.handle_change(4).startswith("ignored")
    assert sem.runs == []


# ------------------------------------------------------------------- console -> playbook
def test_console_run_names_its_devices_for_the_playbook(tools, monkeypatch):
    """Bounce interface targets hosts: "{{ device }}"; without it Ansible stops at once."""
    sem = tools(FakeGLPI(status=7))
    monkeypatch.setattr(actions.data, "customer", lambda code: tenant())
    monkeypatch.setattr(actions.data, "devices", lambda: [{"name": "rtr01", "customer": "acme"},
                                                          {"name": "rtr02", "customer": "acme"}])
    monkeypatch.setattr(actions, "_background", lambda *args: None)
    actions.run_playbook(auth.User(sub="s", email="eko@ioh.example", name="Eko", roles=[auth.ENGINEER, auth.AUTOMATION]),
                         "acme", "3", ["rtr02", "rtr01"], "Change", "4", "bounce the uplink", "interface: Gi0/2")
    run = sem.runs[0]
    assert run["limit"] == "rtr01,rtr02" and run["extra"]["device"] == "rtr01,rtr02"
    assert run["extra"]["interface"] == "Gi0/2" and run["extra"]["glpi_change_id"] == 4
    with pytest.raises(actions.ActionError):
        actions.parse_variables("device: rtr99")       # the form decides the devices, not a typed variable


# ------------------------------------------------------------------- Semaphore device login
def test_new_device_password_overwrites_the_semaphore_key(monkeypatch):
    calls = []

    def request(method, path, payload=None):
        calls.append((method, payload))
        return [{"id": 5, "name": "device-login"}] if method == "GET" else None
    sem = Semaphore("http://semaphore", "token")
    monkeypatch.setattr(sem, "request", request)
    sem.ensure_key(1, "device-login", "login_password", login="netadmin", secret="new", update=True)
    method, payload = calls[-1]
    # Without override_secret Semaphore keeps the old password.
    assert method == "PUT" and payload["override_secret"] and payload["login_password"]["password"] == "new"


# ------------------------------------------------------------------- webhook signatures
@pytest.fixture
def hooks(monkeypatch):
    queued = []
    monkeypatch.setattr(settings, "nautobot_webhook_secret", "nb-secret")
    monkeypatch.setattr(settings, "glpi_webhook_secret", "glpi-secret")
    monkeypatch.setattr(server, "enqueue", lambda name, *args, **kwargs: queued.append(name))
    return TestClient(server.app), queued     # no "with": the worker and scheduler threads stay off


def test_nautobot_webhook_needs_its_signature(hooks):
    client, queued = hooks
    body = json.dumps({"model": "device", "event": "created", "data": {"id": "d1", "name": "rtr01"}}).encode()
    assert client.post("/hooks/nautobot", content=body, headers={"X-Hook-Signature": "forged"}).status_code == 401
    good = hmac.new(b"nb-secret", body, hashlib.sha512).hexdigest()
    assert client.post("/hooks/nautobot", content=body, headers={"X-Hook-Signature": good}).status_code == 200
    assert queued == ["device rtr01"]


def test_glpi_webhook_needs_its_signature(hooks):
    client, queued = hooks
    body, stamp = json.dumps({"item": {"id": 4}}).encode(), str(int(time.time()))
    headers = {"X-GLPI-timestamp": stamp, "X-GLPI-signature": "forged"}
    assert client.post("/hooks/glpi", content=body, headers=headers).status_code == 401
    headers["X-GLPI-signature"] = hmac.new(b"glpi-secret", body + stamp.encode(), hashlib.sha256).hexdigest()
    assert client.post("/hooks/glpi", content=body, headers=headers).status_code == 200
    assert queued == ["glpi change 4"]
