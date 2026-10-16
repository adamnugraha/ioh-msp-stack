"""Unit tests for the MSP Console, the chat alerts and their safety checks (no real tools needed).

Run:  cd glue && python -m pytest -q
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
KIT = HERE.parent.parent
os.environ.setdefault("GLUE_MAPPINGS", str(HERE.parent / "mappings.yaml"))
os.environ.setdefault("CONSOLE_DATA_DIR", tempfile.mkdtemp(prefix="console-test-"))
os.environ.setdefault("CONSOLE_SESSION_SECRET", "test-secret-test-secret-test-secret")
os.environ.setdefault("MSP_DOMAIN", "msp.test.internal")

from msp_glue import chat  # noqa: E402
from msp_glue.common import Tenant  # noqa: E402
from msp_glue.console import actions, auth, data, store  # noqa: E402
from msp_glue.console.actions import ActionError  # noqa: E402
from msp_glue.http import APIError  # noqa: E402


def user(*roles: str) -> auth.User:
    return auth.User(sub="s1", email="eko@ioh.example", name="Eko Test", roles=list(roles))


def tenant(entity: int = 2) -> Tenant:
    return Tenant(id="t1", name="PT ACME Retail Indonesia", code="acme", glpi_entity_id=entity, semaphore_project_id=1)


# ------------------------------------------------------------------------- roles
def test_roles_decide_what_people_may_do():
    assert not user(auth.MANAGER).can_act and not user(auth.MANAGER).can_run
    assert user(auth.ENGINEER).can_act and not user(auth.ENGINEER).can_run
    assert user(auth.ENGINEER, auth.AUTOMATION).can_run
    assert not user(auth.AUTOMATION).can_run          # automation always comes with engineer


def test_claims_keep_only_console_roles():
    u = auth.user_from_claims({"sub": "x", "email": "Eko@IOH.example", "given_name": "Eko", "family_name": "Test",
                               "roles": ["offline_access", "msp-engineer", "default-roles-msp"]})
    assert u.email == "eko@ioh.example" and u.name == "Eko Test" and u.roles == ["msp-engineer"]
    assert auth.user_from_claims({"sub": "x", "preferred_username": "a@b.c"}).name == "a@b.c"


@pytest.mark.parametrize("path, expected", [
    ("/ops", "/ops"), ("/customers/acme?x=1", "/customers/acme?x=1"), ("//evil.example/", "/"),
    ("https://evil.example/", "/"), ("/\\evil.example", "/"), ("", "/"), (None, "/"),
])
def test_after_sign_in_only_console_pages(path, expected):
    assert auth.safe_next(path) == expected


# ---------------------------------------------------------------- playbook input
def test_variables_are_name_value_lines():
    assert actions.parse_variables("interface: Gi0/2\n# a comment\n\nvlan: 20") == {"interface": "Gi0/2", "vlan": "20"}
    assert actions.parse_variables("") == {}


@pytest.mark.parametrize("text", ["ansible_host: 10.0.0.9", "ANSIBLE_user: root", "glpi_ticket_id: 5",
                                  "msp_console_user: x", "no colon here", "1bad: x"])
def test_dangerous_or_bad_variables_are_refused(text):
    with pytest.raises(ActionError):
        actions.parse_variables(text)


class FakeGLPI:
    """Stands in for the GLPI client: items by (itemtype, id); entities by id with their parent."""

    def __init__(self, items: dict, entities: dict):
        self.items, self.entities = items, entities

    def get(self, itemtype, item_id, **_):
        if itemtype == "Entity":
            return {"id": item_id, "entities_id": self.entities.get(item_id, 0)}
        if (itemtype, item_id) not in self.items:
            raise APIError("GLPI GET failed: HTTP 404")
        return self.items[(itemtype, item_id)]


@pytest.fixture
def glpi(monkeypatch):
    def install(items, entities=None):
        fake = FakeGLPI(items, entities or {2: 1, 5: 2, 9: 1})

        @contextmanager
        def opened():
            yield fake
        monkeypatch.setattr(actions.clients, "glpi", opened)
    return install


def test_read_only_template_accepts_an_open_ticket_of_the_customer(glpi):
    glpi({("Ticket", 1): {"status": 2, "entities_id": 2, "is_deleted": 0, "name": "x"}})
    assert actions.check_itil("Ticket", 1, tenant(), "Collect diagnostics")["status"] == 2


def test_ticket_in_a_sub_entity_of_the_customer_counts(glpi):
    glpi({("Ticket", 3): {"status": 1, "entities_id": 5, "is_deleted": 0}})
    actions.check_itil("Ticket", 3, tenant(), "Collect diagnostics")


@pytest.mark.parametrize("item_type, item, template, message", [
    ("Ticket", {"status": 2, "entities_id": 9}, "Collect diagnostics", "another customer"),
    ("Ticket", {"status": 5, "entities_id": 2}, "Collect diagnostics", "solved or closed"),
    ("Ticket", {"status": 2, "entities_id": 2, "is_deleted": 1}, "Collect diagnostics", "trash"),
    ("Ticket", {"status": 2, "entities_id": 2}, "Bounce interface", "approved GLPI change"),
    ("Change", {"status": 10, "entities_id": 2}, "Bounce interface", "not approved yet"),
    ("Change", {"status": 6, "entities_id": 2}, "Collect diagnostics", "finished"),
])
def test_wrong_ticket_or_change_is_refused(glpi, item_type, item, template, message):
    glpi({(item_type, 4): item})
    with pytest.raises(ActionError, match=message):
        actions.check_itil(item_type, 4, tenant(), template)


def test_approved_change_runs_a_change_template(glpi):
    glpi({("Change", 4): {"status": 7, "entities_id": 2}})
    actions.check_itil("Change", 4, tenant(), "Bounce interface")


def test_missing_ticket_is_refused(glpi):
    glpi({})
    with pytest.raises(ActionError, match="no ticket #77"):
        actions.check_itil("Ticket", 77, tenant(), "Collect diagnostics")


def test_actions_check_the_role_first():
    with pytest.raises(ActionError, match="engineer role"):
        actions.acknowledge(user(auth.MANAGER), "25", "")
    with pytest.raises(ActionError, match="engineer role"):
        actions.resync(user(auth.MANAGER), "acme")
    with pytest.raises(ActionError, match="automation role"):
        actions.run_playbook(user(auth.ENGINEER), "acme", "1", ["jkt01-srv01"], "Ticket", "1", "a reason here")


def test_playbook_refused_when_semaphore_would_ignore_the_device_limit(monkeypatch):
    """Without allow_override_limit Semaphore runs the template on the whole inventory."""
    class Semaphore:
        def templates(self, project):
            return [{"id": 7, "name": "Hand-made", "task_params": {"allow_override_limit": False}}]

        def run_task(self, *args, **kwargs):
            raise AssertionError("must not start")
    monkeypatch.setattr(actions.data, "customer", lambda code: tenant())
    monkeypatch.setattr(actions.clients, "semaphore", Semaphore)
    with pytest.raises(ActionError, match="every device"):
        actions.run_playbook(user(auth.ENGINEER, auth.AUTOMATION), "acme", "7", ["jkt01-srv01"], "Ticket", "1",
                             "collect the logs please")
    assert data.limit_allowed({"task_params": {"allow_override_limit": True}})
    assert not data.limit_allowed({})


def test_audit_address_keeps_what_caddy_saw():
    class Req:
        def __init__(self, headers):
            self.headers, self.client = headers, type("C", (), {"host": "192.168.20.5"})()
    assert auth.client_address(Req({})) == "192.168.20.5"
    assert auth.client_address(Req({"cf-connecting-ip": "203.0.113.9"})) == "203.0.113.9 via 192.168.20.5"


class FakeKeycloak:
    """Group membership of one user, as the Keycloak admin API would change it."""

    def __init__(self, groups):
        self.ids = {name: f"g-{i}" for i, name in enumerate(["MSP managers", "MSP engineers", "MSP automation",
                                                              "Other"])}
        self.member = set(groups)

    def user_by_username(self, name):
        return {"id": "u1", "username": name}

    def user_groups(self, user_id):
        return [{"name": n, "id": self.ids[n]} for n in sorted(self.member)]

    def group_by_name(self, name):
        return {"name": name, "id": self.ids[name]}

    def request(self, method, path, *args, **kwargs):
        name = next(n for n, i in self.ids.items() if path.endswith(i))
        (self.member.add if method == "PUT" else self.member.discard)(name)


def test_staff_roles_sets_exactly_the_named_roles(monkeypatch):
    from msp_glue import sso
    kc = FakeKeycloak({"MSP engineers", "MSP automation", "Other"})
    monkeypatch.setattr(sso, "_kc", lambda: kc)
    result = sso.set_staff_roles("eko@ioh.example", ["manager"])
    assert kc.member == {"MSP managers", "Other"}      # groups outside the console are left alone
    assert result["groups"] == "MSP managers"
    sso.set_staff_roles("eko@ioh.example", ["automation"])
    assert kc.member == {"MSP engineers", "MSP automation", "Other"}
    with pytest.raises(APIError):
        sso.set_staff_roles("eko@ioh.example", [])


# ----------------------------------------------------------------- audit trail
def test_audit_entries_are_recorded_and_updated():
    entry = store.record(user(auth.ENGINEER).as_dict(), "re-sync", customer="acme", target="PT ACME", outcome="queued")
    store.update(entry, "done", "tenant acme: done", detail={"jobs": {"1": "tenant acme"}})
    row = store.entries(limit=1, customer="acme")[0]
    assert (row["outcome"], row["result"], row["detail"]["jobs"]) == ("done", "tenant acme: done", {"1": "tenant acme"})
    assert "acme" in store.distinct("customer")


def test_sessions_expire_and_can_be_ended():
    who = {"sub": "s", "email": "a@b.c", "name": "A", "roles": ["msp-manager"]}
    store.save_session("sid-1", who, "idtoken", "refresh", expires=__import__("time").time() + 60)
    assert store.load_session("sid-1")["roles"] == ["msp-manager"]
    store.delete_session("sid-1")
    assert store.load_session("sid-1") is None
    store.save_session("sid-2", who, "", "", expires=__import__("time").time() - 1)
    assert store.load_session("sid-2") is None


def test_session_follows_keycloak_while_it_is_refreshed(monkeypatch):
    """The console session used to end one idle period after the sign-in, even for someone working."""
    import time as clock
    who = {"sub": "s", "email": "a@b.c", "name": "A", "roles": ["msp-engineer"]}
    store.save_session("sid-3", who, "id", "old-refresh", expires=clock.time() + 3660)
    row = store.load_session("sid-3")

    class Response:
        def __init__(self, status, body):
            self.status_code, self._body, self.text = status, body, ""

        def json(self):
            return self._body

        def raise_for_status(self):
            pass

    class Http:
        def post(self, *args, **kwargs):
            return Response(200, {"access_token": "a", "refresh_token": "new-refresh", "refresh_expires_in": 3600})

        def get(self, *args, **kwargs):
            return Response(200, {"sub": "s", "email": "a@b.c", "name": "A", "roles": ["msp-engineer"]})
    monkeypatch.setattr(auth, "metadata", lambda: {"token_endpoint": "t", "userinfo_endpoint": "u"})
    monkeypatch.setattr(auth, "make_session", lambda retries=0: Http())
    later = clock.time() + 3000
    monkeypatch.setattr(auth.time, "time", lambda: later)
    assert auth._recheck(row)["roles"] == ["msp-engineer"]
    monkeypatch.undo()
    refreshed = store.load_session("sid-3")
    assert refreshed["refresh"] == "new-refresh"
    assert refreshed["expires"] >= later + 3600     # moved on with the Keycloak session
    assert refreshed["expires"] <= row["created"] + auth.SESSION_SECONDS


def test_sync_errors_clear_after_a_good_reconcile(monkeypatch):
    from msp_glue import server
    report = type("R", (), {"errors": [], "summary": lambda self: "ok"})()
    monkeypatch.setattr(server.tenants, "managed_tenants", lambda: [])
    monkeypatch.setattr(server.devices, "sync_all", lambda: report)
    server._state["last_error"] = "device jkt01-rtr01: Zabbix timed out"
    server.reconcile()
    assert server._state["last_error"] is None
    server._state["last_error"] = "compliance: Nautobot timed out"
    server.reconcile()
    assert server._state["last_error"] == "compliance: Nautobot timed out"   # not repaired by a sync
    report.errors = ["acme/rtr02: no primary IP"]
    server.reconcile()
    assert server._state["last_error"].startswith("reconcile found 1 device error")


# ---------------------------------------------------------------- formatting
def test_durations_read_well():
    assert data.duration(59) == "1 min"
    assert data.duration(3 * 3600 + 5 * 60) == "3 h 05 min"
    assert data.duration(3 * 86400 + 7200) == "3 days 2 h"


# ------------------------------------------------------------------- the web app
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from msp_glue.console.app import app
    with TestClient(app, base_url="https://console.msp.test.internal") as c:
        yield c


def test_pages_need_a_sign_in(client):
    r = client.get("/ops", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/auth/login?next=/ops")


def test_background_refresh_without_session_gets_401(client):
    assert client.get("/ops", headers={"X-Console-Refresh": "1"}).status_code == 401
    r = client.post("/actions/ack", data={"eventid": "1"})
    assert r.status_code == 401 and "nothing was done" in r.text    # a page that says so, not raw JSON


def test_health_and_security_headers(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY"


def test_csv_cells_cannot_become_formulas():
    from msp_glue.console.app import _csv
    body = _csv("x.csv", ["a", "b"], [{"a": "=HYPERLINK(1)", "b": "plain"}]).body.decode()
    assert "'=HYPERLINK(1)" in body and ",plain" in body


# --------------------------------------------------------------------- chat
def test_chat_severity_mask():
    assert chat.severity_mask(4) == 16 + 32
    assert chat.severity_mask(2) == 4 + 8 + 16 + 32


@pytest.mark.parametrize("delay, ok", [("0", True), ("1m", True), ("90s", True), ("2h", True),
                                       ("30s", False), ("soon", False), ("48h", False)])
def test_chat_delay_must_suit_zabbix_steps(monkeypatch, delay, ok):
    monkeypatch.setitem(chat.settings.mappings, "chat", {"min_severity": 4, "delay": delay})
    if ok:
        assert chat.config()["min_severity"] == 4
    else:
        with pytest.raises(APIError):
            chat.config()


def test_media_type_runs_the_same_script_as_the_source_file():
    media = yaml.safe_load((KIT / "zabbix/media/media_versus_chat.yaml").read_text())["zabbix_export"]["media_types"][0]
    assert media["script"] == (KIT / "zabbix/media/versus_chat.js").read_text()
    names = {p["name"] for p in media["parameters"]}
    assert {"versus_url", "glpi_ticket_id", "console_url", "update_message"} <= names


def test_chat_templates_escape_values_for_telegram_and_email():
    telegram = (KIT / "components/versus/templates/msp_telegram.tmpl").read_text()
    email = (KIT / "components/versus/templates/msp_email.tmpl").read_text()
    for template in (telegram, email):
        for field in (".problem", ".customer", ".device", ".update_message", ".event_url", ".ticket_url",
                      ".console_url"):
            assert f"{{{{ {field} | html }}}}" in template, f"{field} not escaped"
