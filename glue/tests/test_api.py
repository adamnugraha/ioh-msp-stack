"""The console's JSON interface (/api/v1): every answer matches glue/console-ui/src/lib/api/types.ts, changes
need a session, the CSRF token and the right role, and the pages are served on their 1.1 addresses.

Tools are replaced by sample data at the loader level, so no tool is needed. The same contract is checked
against the real tools in the lab (lab/check.py).

Run:  cd glue && python -m pytest -q
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta

import pytest

os.environ.setdefault("GLUE_MAPPINGS", os.path.join(os.path.dirname(__file__), "..", "mappings.yaml"))
os.environ.setdefault("CONSOLE_DATA_DIR", tempfile.mkdtemp(prefix="console-test-"))
os.environ.setdefault("CONSOLE_SESSION_SECRET", "test-secret-test-secret-test-secret")
os.environ.setdefault("MSP_DOMAIN", "msp.test.internal")

from contract import Contract  # noqa: E402

from msp_glue.common import Tenant  # noqa: E402
from msp_glue.console import actions, api, auth, data, detail, investigate, store, views  # noqa: E402
from msp_glue.console.actions import ActionError  # noqa: E402

CONTRACT = Contract.load()
CONSOLE = "https://console.msp.test.internal"


def conforms(value, type_name: str) -> None:
    problems = CONTRACT.check(value, type_name)
    assert not problems, "\n".join(problems[:25])


def person(*roles: str) -> auth.User:
    return auth.User(sub="s1", email="eko@ioh.example", name="Eko Test", roles=list(roles))


ENGINEER = person(auth.ENGINEER)
AUTOMATOR = person(auth.ENGINEER, auth.AUTOMATION)
MANAGER = person(auth.MANAGER)


# ============================================================================ sample data
@pytest.fixture
def sample(monkeypatch):
    """Every loader answers with a small, realistic sample: two customers, four devices."""
    now = data.now()
    acme = Tenant(id="t-acme", name="PT ACME Retail Indonesia", code="acme", tier="gold", customer_id="C-1001",
                  contact_email="it@acme.example", glpi_entity_id=2, zabbix_hostgroup_id="21", semaphore_project_id=1,
                  custom_fields={"msp_provisioning_status": "provisioned"})
    bankx = Tenant(id="t-bankx", name="PT Bank Contoh Tbk", code="bankx", regulated=True, glpi_entity_id=3,
                   zabbix_hostgroup_id="22", semaphore_project_id=2)

    def device(i, name, customer, role, **extra):
        row = {"id": f"d{i}", "name": name, "serial": f"S{i}", "status": "Active", "role": role, "platform": "cisco_ios",
               "driver": "cisco_ios", "model": "ISR4331", "site": "Jakarta HQ", "site_id": "l1", "region": "Jakarta",
               "customer": customer, "ip": f"172.30.0.{200 + i}", "gc_scope": True, "monitoring": True,
               "drift_ticket": None, "asset_id": 10 + i, "itemtype": "NetworkEquipment"}
        row.update(extra)
        return row

    devices = [
        device(1, "jkt01-rtr01", "acme", "router", drift_ticket=7),
        device(2, "jkt01-srv01", "acme", "server", platform="linux", gc_scope=False, itemtype="Computer"),
        device(3, "jkt01-sw09", "acme", "switch", status="Planned", ip="", gc_scope=False),
        device(4, "ckr01-rtr01", "bankx", "router", site="Cikarang DC", site_id="l2"),
    ]
    hosts = {
        "d1": {"hostid": "10601", "name": "acme jkt01-rtr01", "state": "up", "customer": "acme", "device": "jkt01-rtr01",
               "error": "", "maintenance": False},
        "d2": {"hostid": "10602", "name": "acme jkt01-srv01", "state": "down", "customer": "acme",
               "device": "jkt01-srv01", "error": "timed out", "maintenance": True},
        "d4": {"hostid": "10604", "name": "bankx ckr01-rtr01", "state": "up", "customer": "bankx",
               "device": "ckr01-rtr01", "error": "", "maintenance": False},
    }
    problems = [
        {"eventid": "501", "name": "Unavailable by ICMP ping", "severity": 4, "severity_name": "High",
         "started": now - timedelta(hours=1), "age": "1 h", "acknowledged": False, "suppressed": True, "customer": "acme",
         "customer_name": acme.name, "device": "jkt01-srv01", "site": "Jakarta HQ", "ticket_id": "42",
         "ticket_link": "https://glpi.msp.test.internal/front/ticket.form.php?id=42", "opdata": "", "updates": [],
         "zabbix_link": "https://zabbix.msp.test.internal/tr_events.php?triggerid=1&eventid=501"},
        {"eventid": "502", "name": "High CPU utilisation", "severity": 2, "severity_name": "Warning",
         "started": now - timedelta(minutes=20), "age": "20 min", "acknowledged": True, "suppressed": False,
         "customer": "bankx", "customer_name": bankx.name, "device": "ckr01-rtr01", "site": "Cikarang DC",
         "ticket_id": "", "ticket_link": None, "opdata": "Current utilisation: 93 %",
         "updates": [{"at": now - timedelta(minutes=5), "who": "Eko Test", "message": "Looking at it."}],
         "zabbix_link": "https://zabbix.msp.test.internal/tr_events.php?triggerid=2&eventid=502"},
    ]
    tickets = [{"id": 42, "name": "jkt01-srv01 unreachable", "status": "Assigned", "priority": 4, "priority_name": "High",
                "customer": "acme", "opened": now - timedelta(hours=1), "due": now + timedelta(hours=3), "late": False,
                "group": "MSP NOC", "technician": "", "type": "Incident", "source": "Monitoring", "device": "jkt01-srv01",
                "link": "https://glpi.msp.test.internal/front/ticket.form.php?id=42"},
               {"id": 43, "name": "New VLAN for the shop floor", "status": "New", "priority": 3,
                "priority_name": "Medium", "customer": "bankx", "opened": now - timedelta(days=2), "due": None,
                "late": True, "group": "", "technician": "", "type": "Request", "source": "Email", "device": None,
                "link": "https://glpi.msp.test.internal/front/ticket.form.php?id=43"}]
    changes = [{"id": 9, "name": "Bounce Gi0/2", "status": "Accepted", "customer": "acme",
                "category": "Change > Network", "template": "Bounce interface", "planned_start": now + timedelta(hours=1),
                "planned_end": now + timedelta(hours=2), "risk": "Low", "devices": ["jkt01-rtr01"],
                "requester": "Eko Test", "maintenance_id": "77", "opened": now - timedelta(days=1),
                "link": "https://glpi.msp.test.internal/front/change.form.php?id=9"},
               {"id": 10, "name": "Firmware update", "status": "Approval", "customer": "bankx", "category": "",
                "template": None, "planned_start": None, "planned_end": None, "risk": "High", "devices": [],
                "requester": "Rina", "maintenance_id": None, "opened": now - timedelta(days=3),
                "link": "https://glpi.msp.test.internal/front/change.form.php?id=10"}]
    maintenance = [{"id": "77", "name": "Bounce Gi0/2 (acme)", "customer": "acme", "devices": ["jkt01-rtr01"],
                    "start": now + timedelta(hours=1), "end": now + timedelta(hours=2), "collect_data": True,
                    "change_id": 9, "created_by": "Eko Test", "state": "upcoming", "link": "https://zabbix/x"},
                   {"id": "78", "name": "Power work (acme)", "customer": "acme", "devices": None,
                    "start": now - timedelta(minutes=10), "end": now + timedelta(minutes=50), "collect_data": False,
                    "change_id": None, "created_by": "Zabbix", "state": "active", "link": "https://zabbix/y"}]
    start = data.month_start()
    previous = data.month_start(1)
    availability = {"acme": {"slo": 99.9, "slaid": "1", "sli": 99.95, "downtime": 120, "budget": 2000,
                             "previous": 100.0, "periods": [
                                 {"month": previous.strftime("%Y-%m"), "start": previous, "end": start, "sli": 100.0,
                                  "downtime": 0, "budget": 2592},
                                 {"month": start.strftime("%Y-%m"), "start": start, "end": data.next_month(start),
                                  "sli": 99.95, "downtime": 120, "budget": 2000}]}}
    compliance = {"d1": {"rules": 3, "failed": ["ntp"]}, "d4": {"rules": 3, "failed": []}}
    backups = {"d1": {"last_success": now - timedelta(hours=2), "last_attempt": now - timedelta(hours=2),
                      "compliance_checked": now - timedelta(hours=2)},
               "d4": {"last_success": None, "last_attempt": now - timedelta(hours=1), "compliance_checked": None}}
    task = {"id": 31, "status": "success", "template": "Collect diagnostics", "created": now - timedelta(hours=3),
            "finished": now - timedelta(hours=3) + timedelta(minutes=2), "devices": ["jkt01-srv01"],
            "message": "MSP Console, eko@ioh.example, Ticket #42: collect the logs", "by": "eko@ioh.example",
            "item": "Ticket #42", "customer": "acme", "link": "https://semaphore/project/1/history?t=31"}
    automation = {"project_id": 1, "tasks": [task, {**task, "id": 32, "status": "", "finished": None}],
                  "templates": [{"id": 5, "name": "Collect diagnostics", "description": "Read-only", "limit": True},
                                {"id": 6, "name": "Bounce interface", "description": "", "limit": True},
                                {"id": 7, "name": "Hand-made", "description": "", "limit": False}]}
    glue = {"jobs": [{"id": 3, "name": "tenant acme", "status": "done", "queued": "2026-10-17 10:00:00",
                      "started": "2026-10-17 10:00:01", "finished": "2026-10-17 10:00:09", "result": "ok",
                      "requested_by": "eko@ioh.example"},
                     {"id": 4, "name": "devices acme", "status": "running", "queued": "2026-10-17 10:00:00",
                      "started": None, "finished": None, "result": None}],
            "queue": 1, "last_error": "device jkt01-sw09: no primary IP", "last_error_at": "2026-10-17 09:58:00",
            "last_reconcile": "2026-10-17 09:50:00"}
    health = [{"tool": "Nautobot", "ok": True, "detail": "3.2.6", "ms": 40},
              {"tool": "Zabbix", "ok": False, "detail": "ConnectionError: refused", "ms": None},
              {"tool": "Keycloak", "ok": True, "detail": "realm msp", "ms": 12}]
    stats = {"acme": {"opened": 5, "solved": 4, "late": 1, "own_late": 1, "resolve_late": 0, "mttr_hours": 3.5}}
    sites = {"acme": [{"id": "l1", "name": "Jakarta HQ", "region": "Jakarta"}],
             "bankx": [{"id": "l2", "name": "Cikarang DC", "region": "West Java"}]}
    circuit = {"id": "cx", "cid": "IOH-MPLS-0042", "commit_rate": 100000, "status": {"name": "Active"},
               "provider": {"name": "IOH"}, "circuit_type": {"name": "MPLS"}}
    interfaces = [
        {"id": "i1", "name": "Gi0/0", "description": "WAN to IOH", "enabled": True, "mgmt_only": False, "type": "1000base-t",
         "speed": 1000000, "device": {"id": "d1", "name": "jkt01-rtr01"}, "cable": {"id": "c1", "status": {"name": "Connected"}},
         "cable_peer_interface": None, "cable_peer_circuit_termination": {"id": "ct1", "term_side": "A", "circuit": circuit}},
        {"id": "i2", "name": "Gi0/1", "description": "LAN", "enabled": True, "mgmt_only": False, "type": "1000base-t",
         "speed": 1000000, "device": {"id": "d1", "name": "jkt01-rtr01"}, "cable": {"id": "c2", "status": {"name": "Connected"}},
         "cable_peer_interface": {"id": "i3", "name": "eth0", "device": {"id": "d2", "name": "jkt01-srv01"}},
         "cable_peer_circuit_termination": None},
        {"id": "i3", "name": "eth0", "description": "", "enabled": True, "mgmt_only": False, "type": "1000base-t",
         "speed": None, "device": {"id": "d2", "name": "jkt01-srv01"}, "cable": {"id": "c2", "status": {"name": "Connected"}},
         "cable_peer_interface": {"id": "i2", "name": "Gi0/1", "device": {"id": "d1", "name": "jkt01-rtr01"}},
         "cable_peer_circuit_termination": None},
        {"id": "i4", "name": "Gi0/3", "description": "spare", "enabled": False, "mgmt_only": False, "type": "1000base-t",
         "speed": 1000000, "device": {"id": "d1", "name": "jkt01-rtr01"}, "cable": None, "cable_peer_interface": None,
         "cable_peer_circuit_termination": None},
    ]
    clock = str(int(now.timestamp()))
    items = [
        {"itemid": "1", "key_": "icmppingsec", "name": "ICMP response time", "units": "s", "value_type": "0",
         "lastvalue": "0.0012", "lastclock": clock, "delay": "1m", "state": "0", "tags": []},
        {"itemid": "2", "key_": "icmppingloss", "name": "ICMP loss", "units": "%", "value_type": "0", "lastvalue": "0",
         "lastclock": clock, "delay": "1m", "state": "0", "tags": []},
        {"itemid": "3", "key_": "net.if.in[ifHCInOctets.1]", "name": "Interface Gi0/0(WAN): Bits received",
         "units": "bps", "value_type": "3", "lastvalue": "1200000", "lastclock": clock, "delay": "3m", "state": "0",
         "tags": [{"tag": "interface", "value": "Gi0/0"}]},
        {"itemid": "4", "key_": "net.if.out[ifHCOutOctets.1]", "name": "Interface Gi0/0(WAN): Bits sent",
         "units": "bps", "value_type": "3", "lastvalue": "300000", "lastclock": clock, "delay": "3m", "state": "0",
         "tags": [{"tag": "interface", "value": "Gi0/0"}]},
        {"itemid": "5", "key_": "net.if.status[ifOperStatus.1]", "name": "Interface Gi0/0(WAN): Operational status",
         "units": "", "value_type": "3", "lastvalue": "1", "lastclock": clock, "delay": "1m", "state": "0",
         "tags": [{"tag": "interface", "value": "Gi0/0"}]},
        {"itemid": "6", "key_": "net.if.in.errors[ifInErrors.1]", "name": "Interface Gi0/0(WAN): Inbound errors",
         "units": "", "value_type": "3", "lastvalue": "0.5", "lastclock": clock, "delay": "3m", "state": "0",
         "tags": [{"tag": "interface", "value": "Gi0/0"}]},
        {"itemid": "7", "key_": "system.cpu.util", "name": "CPU utilization", "units": "%", "value_type": "0",
         "lastvalue": "12", "lastclock": clock, "delay": "1m", "state": "0", "tags": []},
        {"itemid": "8", "key_": "vfs.fs.dependent.size[/,pused]", "name": "FS [/]: Space: Used, in %", "units": "%",
         "value_type": "0", "lastvalue": "41", "lastclock": clock, "delay": "0", "state": "0", "tags": []},
        {"itemid": "9", "key_": "net.if.in[ifHCInOctets.9]", "name": "Interface Gi0/9: Bits received", "units": "bps",
         "value_type": "3", "lastvalue": "10", "lastclock": clock, "delay": "3m", "state": "0",
         "tags": [{"tag": "interface", "value": "Gi0/9"}]},
    ]
    history = [{"at": now - timedelta(hours=2), "commit": "abc1234", "changedLines": 2,
                "message": "Backup jkt01-rtr01", "link": "https://forgejo/msp/backups-acme/commit/abc1234"}]
    rules = [{"rule": "ntp", "compliant": False, "missing": ["ntp server 10.255.0.2"], "extra": []},
             {"rule": "snmp", "compliant": True, "missing": [], "extra": []}]

    monkeypatch.setattr(data, "customers", lambda: [acme, bankx])
    monkeypatch.setattr(data, "devices", lambda: devices)
    monkeypatch.setattr(data, "hosts", lambda: hosts)
    monkeypatch.setattr(data, "problems", lambda: problems)
    monkeypatch.setattr(data, "open_tickets", lambda: tickets)
    monkeypatch.setattr(data, "changes", lambda: changes)
    monkeypatch.setattr(data, "maintenances", lambda: maintenance)
    monkeypatch.setattr(data, "availability", lambda: availability)
    monkeypatch.setattr(data, "ticket_stats", lambda start, end=None: stats)
    monkeypatch.setattr(data, "compliance", lambda: compliance)
    monkeypatch.setattr(data, "backups", lambda: backups)
    monkeypatch.setattr(data, "automation", lambda code: automation if code == "acme" else
                        {"project_id": None, "templates": [], "tasks": []})
    monkeypatch.setattr(data, "last_runs", lambda: {"acme": {"status": "success", "when": now - timedelta(hours=3),
                                                             "template": "Collect diagnostics"}})
    monkeypatch.setattr(data, "glue_jobs", lambda: glue)
    monkeypatch.setattr(data, "health", lambda: health)
    monkeypatch.setattr(data, "grafana_org", lambda tenant: 4 if tenant.code == "acme" else None)
    monkeypatch.setattr(data, "sites", lambda code: sites.get(code, []))
    monkeypatch.setattr(data, "downtime_by_day", lambda code: [{"day": start.strftime("%Y-%m-%d"), "minutes": 2}])
    monkeypatch.setattr(detail, "nautobot_interfaces",
                        lambda ids: [i for i in interfaces if i["device"]["id"] in ids])
    monkeypatch.setattr(detail, "items", lambda hostid: items if hostid == "10601" else [])
    monkeypatch.setattr(detail, "backup_history", lambda d, code: history if d["name"] == "jkt01-rtr01" else [])
    monkeypatch.setattr(detail, "compliance_rules", lambda d: rules)
    return {"acme": acme, "bankx": bankx, "devices": devices, "now": now}


class Request:
    def __init__(self):
        self.session: dict = {}
        self.headers: dict = {}


# ============================================================================== contract
def test_contract_reads_types_ts():
    assert {"Overview", "CustomerDetail", "DeviceDetail", "Investigation", "Me"} <= set(CONTRACT.interfaces)
    assert CONTRACT.check({"code": "x"}, "Site") == ["Site.id: missing", "Site.name: missing", "Site.region: missing",
                                                      "Site.code: not in types.ts"]


def test_me(sample):
    me = views.me(AUTOMATOR, Request(), False)
    conforms(me, "Me")
    assert me["canRun"] and me["roles"] == ["engineer", "automation"] and me["investigate"] is False
    assert me["accountUrl"].endswith("/realms/msp/account") and me["csrf"]


def test_overview(sample):
    answer = views.overview(ENGINEER)
    conforms(answer, "Overview")
    assert [p["eventid"] for p in answer["problems"]] == ["501", "502"]
    assert answer["glue"]["lastError"]["message"].startswith("device jkt01-sw09")
    assert [c["id"] for c in answer["changesToday"]] == [9]
    assert {m["id"] for m in answer["maintenance"]} == {"77", "78"}
    assert views.overview(MANAGER)["glue"] is None      # integration jobs are for engineers


def test_customer_summaries(sample):
    rows = {r["code"]: r for r in views.summaries()}
    for row in rows.values():
        conforms(row, "CustomerSummary")
    acme = rows["acme"]
    assert (acme["devices"], acme["active"], acme["up"], acme["down"], acme["downInMaintenance"]) == (3, 2, 1, 1, 1)
    assert acme["changesToday"] == 1 and acme["maintenanceNow"] == 1
    assert acme["sla"]["slo"] == 99.9 and acme["sla"]["allowed"] > 0
    assert rows["bankx"]["sla"] is None and rows["bankx"]["backupFailed"] == 1


def test_device_index(sample):
    rows = views.device_index()
    for row in rows:
        conforms(row, "DeviceIndexRow")
    assert {r["name"]: r["state"] for r in rows}["jkt01-sw09"] is None


def test_customer_detail(sample):
    answer = views.customer_detail("acme")
    conforms(answer, "CustomerDetail")
    assert answer["links"]["grafana"].endswith("orgId=4") and "glpi" in answer["links"]
    assert [d["name"] for d in answer["devices"]] == ["jkt01-rtr01", "jkt01-srv01", "jkt01-sw09"]   # active first
    assert answer["devices"][0]["links"]["backups"].endswith("/jakarta-hq/jkt01-rtr01.cfg")
    assert answer["automation"]["templates"][1]["changeOnly"] is ("Bounce interface" in actions.change_only_templates())
    assert answer["automation"]["tasks"][1]["status"] == "waiting"
    assert views.customer_detail("nobody") is None


def test_topology_has_cables_once_and_the_provider_network(sample):
    answer = detail.topology("acme")
    conforms(answer, "Topology")
    kinds = sorted((link["kind"], link["a"], link["b"]) for link in answer["links"])
    assert kinds == [("cable", "d1", "d2"), ("circuit", "d1", "provider-acme")]
    nodes = {n["id"]: n for n in answer["nodes"]}
    assert nodes["provider-acme"]["state"] == "external" and nodes["d3"]["state"] == "planned"
    assert {link["kind"]: link["state"] for link in answer["links"]} == {"cable": "down", "circuit": "up"}


def test_device_detail(sample):
    answer = views.device_detail("acme", "jkt01-rtr01")
    conforms(answer, "DeviceDetail")
    ifaces = {i["name"]: i for i in answer["interfaces"]}
    assert ifaces["Gi0/0"]["circuit"] == "IOH-MPLS-0042" and ifaces["Gi0/0"]["inBps"] == 1200000
    assert ifaces["Gi0/0"]["errorsPerMin"] == 30 and ifaces["Gi0/0"]["speed"] == 1_000_000_000
    assert ifaces["Gi0/1"]["peer"] == {"device": "jkt01-srv01", "interface": "eth0"}
    assert ifaces["Gi0/3"]["status"] == "admin-down"
    assert ifaces["Gi0/9"]["description"] == "Seen by Zabbix only"
    assert {c["id"] for c in answer["charts"]} == {"icmp_rtt", "icmp_loss", "cpu", "fs:/", "if:Gi0/0", "if:Gi0/9"}
    assert answer["compliance"][0]["rule"] == "ntp" and answer["backups"][0]["commit"] == "abc1234"
    server = views.device_detail("acme", "jkt01-srv01")
    conforms(server, "DeviceDetail")
    assert server["compliance"] == [] and server["tasks"][0]["id"] == 31
    assert all(i["status"] == "down" and i["inBps"] is None for i in server["interfaces"])   # the host is down
    assert views.device_detail("acme", "ckr01-rtr01") is None       # another customer's device


def test_metrics_scale_and_show_gaps(sample, monkeypatch):
    now = int(sample["now"].timestamp())

    class Zabbix:
        def call(self, method, params):
            if method == "history.get":
                # one reading a minute, then nothing for 20 minutes, then readings again
                clocks = [now - 3600 + 60 * i for i in range(20)] + [now - 1200 + 60 * i for i in range(20)]
                return [{"clock": str(c), "value": "0.002"} for c in clocks]
            return [{"clock": str(now - 7200), "value_avg": "0.004"}]
    monkeypatch.setattr(detail.clients, "zabbix", Zabbix)
    device = sample["devices"][0]
    answer = detail.metrics(device, "1h", ["icmp_rtt", "if:Gi0/0", "not-a-chart"])
    conforms(answer, "DeviceMetrics")
    points = answer["charts"][0]["series"][0]["points"]
    assert points[0][1] == 2.0                                  # seconds became milliseconds
    assert [p for p in points if p[1] is None]                  # the 20-minute gap is visible
    assert [c["id"] for c in answer["charts"]] == ["icmp_rtt", "if:Gi0/0"]
    assert [s["name"] for s in answer["charts"][1]["series"]] == ["In", "Out"]
    week = detail.metrics(device, "7d", ["cpu"])
    conforms(week, "DeviceMetrics")
    assert week["source"] == "trends" and week["step"] == 3600


def test_work_service_audit_sessions_glue_health(sample):
    conforms(views.work(), "WorkList")
    report = views.service(None)
    conforms(report, "ServiceReport")
    assert report["label"].endswith("(so far)") and len(report["monthsAvailable"]) == 3
    assert report["totals"]["withSla"] == 1 and report["totals"]["met"] == 1
    last = views.service(data.month_start(1).strftime("%Y-%m"))
    conforms(last, "ServiceReport")
    assert next(r for r in last["rows"] if r["code"] == "acme")["sla"]["sli"] == 100.0
    assert views.service("1999-01")["month"] == report["month"]     # unknown month: this month
    store.record(ENGINEER.as_dict(), "re-sync", customer="acme", target="PT ACME", outcome="done")
    conforms(views.audit(), "AuditPage")
    store.save_session("sid-api", {**ENGINEER.as_dict()}, "", "", expires=data.time.time() + 600, address="10.0.0.5")
    for row in views.sessions():
        conforms(row, "SessionRow")
    conforms(views.glue(), "GlueStatus")
    tools = views.health()
    for row in tools:
        conforms(row, "ToolStatus")
    by_tool = {t["tool"]: t for t in tools}
    assert by_tool["Nautobot"]["version"] == "3.2.6" and by_tool["Keycloak"]["version"] is None
    assert by_tool["Zabbix"]["ok"] is False and by_tool["Keycloak"]["signIn"]["enabled"]


def test_service_csv_rows(sample):
    rows = views.service_csv_rows(None)
    assert {r["code"] for r in rows} == {"acme", "bankx"}
    assert next(r for r in rows if r["code"] == "acme")["downtime_seconds"] == 120


# ========================================================================= access over HTTP
@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from msp_glue.console import app as console_app
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>MSP Console</title><div id=root></div>")
    monkeypatch.setattr(console_app, "UI", ui)
    monkeypatch.setattr(console_app, "BRANDING", tmp_path / "branding")
    with TestClient(console_app.app, base_url=CONSOLE) as c:
        yield c


def sign_in(monkeypatch, who: auth.User | None):
    monkeypatch.setattr(auth, "current_user", lambda request: who)


def test_reads_need_a_session(client, monkeypatch):
    sign_in(monkeypatch, None)
    for path in ("/api/v1/me", "/api/v1/overview", "/api/v1/customers/acme", "/api/v1/audit.csv"):
        r = client.get(path)
        assert r.status_code == 401 and r.json()["detail"].startswith("Signed out"), path


def test_changes_need_session_token_origin_and_role(client, monkeypatch, sample):
    sign_in(monkeypatch, None)
    assert client.post("/api/v1/customers/acme/resync").status_code == 401
    sign_in(monkeypatch, ENGINEER)
    assert client.post("/api/v1/customers/acme/resync").status_code == 403          # no CSRF token yet
    token = client.get("/api/v1/me").json()["csrf"]
    calls = []
    monkeypatch.setattr(actions, "resync", lambda user, code, address="": calls.append((user.email, code, address))
                        or actions.Result("queued", 7))
    r = client.post("/api/v1/customers/acme/resync", headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403 and "out of date" in r.json()["detail"]
    r = client.post("/api/v1/customers/acme/resync", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"})
    assert r.status_code == 403 and "evil.example" in r.json()["detail"]
    r = client.post("/api/v1/customers/acme/resync", headers={"X-CSRF-Token": token, "Origin": CONSOLE})
    assert r.status_code == 200 and r.json() == {"message": "queued", "auditId": 7}
    conforms(r.json(), "ActionResult")
    assert calls[0][:2] == ("eko@ioh.example", "acme")


def test_refusals_keep_their_status(client, monkeypatch, sample):
    sign_in(monkeypatch, MANAGER)
    token = client.get("/api/v1/me").json()["csrf"]
    headers = {"X-CSRF-Token": token}
    r = client.post("/api/v1/customers/acme/config-check", headers=headers)
    assert r.status_code == 403 and "engineer role" in r.json()["detail"]
    sign_in(monkeypatch, ENGINEER)
    r = client.post("/api/v1/customers/acme/tickets", headers=headers, json={
        "customer": "acme", "kind": "Incident", "title": "x", "description": "too short?", "priority": 3, "devices": []})
    assert r.status_code == 400 and "title" in r.json()["detail"]
    r = client.post("/api/v1/customers/acme/tickets", headers=headers, json={"kind": "Escalation"})
    assert r.status_code == 422                                    # not a request the pages send

    def broken(*args, **kwargs):
        raise RuntimeError("GLPI went away")
    monkeypatch.setattr(actions, "create_ticket", broken)
    r = client.post("/api/v1/customers/acme/tickets", headers=headers, json={
        "customer": "acme", "kind": "Incident", "title": "Printer offline", "description": "The shop printer is offline.",
        "priority": 3, "devices": []})
    assert r.status_code == 502 and "GLPI went away" in r.json()["detail"]


def test_investigate_is_off_without_a_model(client, monkeypatch, sample):
    monkeypatch.delenv("CONSOLE_AI_URL", raising=False)
    sign_in(monkeypatch, ENGINEER)
    me = client.get("/api/v1/me").json()
    assert me["investigate"] is False
    r = client.post("/api/v1/problems/501/investigations", headers={"X-CSRF-Token": me["csrf"]})
    assert r.status_code == 409 and "CONSOLE_AI_URL" in r.json()["detail"]
    assert client.get("/api/v1/investigations/inv-unknown").status_code == 404


def test_reads_answer_the_contract_over_http(client, monkeypatch, sample):
    sign_in(monkeypatch, AUTOMATOR)
    for path, type_name in (("/api/v1/me", "Me"), ("/api/v1/overview", "Overview"),
                            ("/api/v1/customers/acme", "CustomerDetail"), ("/api/v1/customers/acme/topology", "Topology"),
                            ("/api/v1/customers/acme/devices/jkt01-rtr01", "DeviceDetail"), ("/api/v1/work", "WorkList"),
                            ("/api/v1/service", "ServiceReport"), ("/api/v1/audit?outcome=done", "AuditPage"),
                            ("/api/v1/glue", "GlueStatus")):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
        assert r.headers["cache-control"] == "no-store"
        conforms(r.json(), type_name)
    for path, type_name in (("/api/v1/customers", "CustomerSummary"), ("/api/v1/devices", "DeviceIndexRow"),
                            ("/api/v1/health", "ToolStatus"), ("/api/v1/sessions", "SessionRow")):
        for row in client.get(path).json():
            conforms(row, type_name)
    assert client.get("/api/v1/customers/nobody").status_code == 404
    assert client.get("/api/v1/customers/acme/devices/ckr01-rtr01").status_code == 404
    r = client.get("/api/v1/no/such/thing")
    assert r.status_code == 404 and r.json()["detail"].startswith("No such address")
    assert client.post("/api/v1/no/such/thing").status_code == 404


def test_csv_exports(client, monkeypatch, sample):
    sign_in(monkeypatch, MANAGER)
    r = client.get("/api/v1/service.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("month,code,name,tier")
    store.record(MANAGER.as_dict(), "=cmd|' /C calc'!A0", outcome="done")
    r = client.get("/api/v1/audit.csv")
    assert "'=cmd" in r.text and 'attachment; filename="console-audit-' in r.headers["content-disposition"]
    old = client.get("/service.csv?month=2026-09", follow_redirects=False)
    assert old.status_code == 307 and old.headers["location"] == "/api/v1/service.csv?month=2026-09"


def test_csv_cells_cannot_become_formulas():
    body = api._csv("x.csv", ["a", "b"], [{"a": "=HYPERLINK(1)", "b": "plain"}, {"a": "@SUM(1)", "b": "-2+3"}]).body.decode()
    assert "'=HYPERLINK(1)" in body and ",plain" in body and "'@SUM(1)" in body and "'-2+3" in body


def test_pages_are_the_react_application(client, monkeypatch):
    sign_in(monkeypatch, None)
    r = client.get("/customers/acme/devices/jkt01-rtr01?tab=x", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login?next=/customers/acme/devices/jkt01-rtr01%3Ftab%3Dx"
    sign_in(monkeypatch, ENGINEER)
    for path in ("/", "/ops", "/customers", "/customers/acme", "/customers/acme/network", "/changes", "/service",
                 "/audit", "/tools"):
        r = client.get(path)
        assert r.status_code == 200 and "<div id=root>" in r.text, path
        assert r.headers["cache-control"] == "no-store"
    csp = client.get("/ops").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert client.get("/nothing-here").status_code == 404


def test_pages_say_when_they_are_not_built(client, monkeypatch, tmp_path):
    from msp_glue.console import app as console_app
    monkeypatch.setattr(console_app, "UI", tmp_path / "missing")
    sign_in(monkeypatch, ENGINEER)
    r = client.get("/ops")
    assert r.status_code == 503 and "docker compose build" in r.text
    health = client.get("/healthz").json()
    assert health["ui"] is False and health["version"] == console_app.VERSION


def test_brand_logo(client, tmp_path):
    assert client.get("/ui/brand/logo").status_code == 204
    (tmp_path / "branding").mkdir()
    (tmp_path / "branding" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    r = client.get("/ui/brand/logo")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-cache"


def test_message_page_for_errors(client):
    r = client.get("/auth/signed-out")
    assert r.status_code == 200 and "You are signed out" in r.text and "/static/message.css" in r.text


# ================================================================================ actions
def test_ticket_and_change_checks(sample):
    with pytest.raises(ActionError, match="title"):
        actions.create_ticket(ENGINEER, "acme", "Incident", "x", "A long enough description", 3, [])
    with pytest.raises(ActionError, match="Not a device of PT ACME"):
        actions.create_ticket(ENGINEER, "acme", "Incident", "Printer offline", "A long enough description", 3,
                              ["ckr01-rtr01"])
    now = sample["now"]
    with pytest.raises(ActionError, match="end must be after"):
        actions.create_ticket(ENGINEER, "acme", "Change", "Bounce the WAN port", "Bounce it to clear errors", 3,
                              ["jkt01-rtr01"], planned_start=(now + timedelta(hours=2)).isoformat(),
                              planned_end=(now + timedelta(hours=1)).isoformat(), rollback="Set it back up again")
    with pytest.raises(ActionError, match="back-out plan"):
        actions.create_ticket(ENGINEER, "acme", "Change", "Bounce the WAN port", "Bounce it to clear errors", 3,
                              ["jkt01-rtr01"], planned_start=(now + timedelta(hours=1)).isoformat(),
                              planned_end=(now + timedelta(hours=2)).isoformat(), rollback="no")
    with pytest.raises(ActionError, match="No GLPI change category runs"):
        actions.create_ticket(ENGINEER, "acme", "Change", "Bounce the WAN port", "Bounce it to clear errors", 3,
                              ["jkt01-rtr01"], planned_start=(now + timedelta(hours=1)).isoformat(),
                              planned_end=(now + timedelta(hours=2)).isoformat(), rollback="Set it back up again",
                              template="Format the disk")
    with pytest.raises(actions.Forbidden):
        actions.create_ticket(MANAGER, "acme", "Incident", "Printer offline", "A long enough description", 3, [])


def test_maintenance_checks(sample):
    start = (sample["now"] + timedelta(minutes=30)).isoformat()
    with pytest.raises(ActionError, match="regulated customer"):
        actions.create_maintenance(ENGINEER, "bankx", ["ckr01-rtr01"], "Router reload", start, 60, True, None)
    with pytest.raises(ActionError, match="15 minutes to 24 hours"):
        actions.create_maintenance(ENGINEER, "acme", ["jkt01-rtr01"], "Router reload", start, 5, True, None)
    with pytest.raises(ActionError, match="in the past"):
        actions.create_maintenance(ENGINEER, "acme", ["jkt01-rtr01"], "Router reload",
                                   (sample["now"] - timedelta(hours=1)).isoformat(), 60, True, None)
    with pytest.raises(ActionError, match="Not monitored in Zabbix"):
        actions.create_maintenance(ENGINEER, "acme", ["jkt01-sw09"], "Switch swap", start, 60, True, None)
    with pytest.raises(ActionError, match="date and time"):
        actions.create_maintenance(ENGINEER, "acme", None, "Power work", "tomorrow", 60, True, None)


def test_device_checks(sample):
    with pytest.raises(ActionError, match="lower-case"):
        actions.add_device(ENGINEER, "acme", "l1", "JKT 01", "switch", "", "C9200", "Active", "10.0.0.9", "", True)
    with pytest.raises(ActionError, match="exists already"):
        actions.add_device(ENGINEER, "acme", "l1", "jkt01-rtr01", "router", "", "ISR4331", "Active", "10.0.0.9", "", True)
    with pytest.raises(ActionError, match="one of the customer's sites"):
        actions.add_device(ENGINEER, "acme", "l2", "jkt01-sw10", "switch", "", "C9200", "Active", "10.0.0.9", "", True)
    with pytest.raises(ActionError, match="management address"):
        actions.add_device(ENGINEER, "acme", "l1", "jkt01-sw10", "switch", "", "C9200", "Active", "", "", True)
    with pytest.raises(ActionError, match="address of another device"):
        actions.add_device(ENGINEER, "acme", "l1", "jkt01-sw10", "switch", "", "C9200", "Active", "172.30.0.201", "",
                           True)


def test_notes_need_text():
    with pytest.raises(ActionError, match="at least 10"):
        actions.post_note(ENGINEER, "Ticket", 42, "short")
    with pytest.raises(actions.Forbidden):
        actions.post_note(MANAGER, "Ticket", 42, "A note that is long enough")


@pytest.mark.parametrize("line, shown", [
    ("snmp-server community S3cret RO 10", "snmp-server community <hidden> RO 10"),
    ("username admin privilege 15 secret 9 $9$abcdef", "username admin privilege 15 secret 9 <hidden>"),
    ("ntp authentication-key 1 md5 0123 7", "ntp authentication-key 1 md5 <hidden> 7"),
    ("snmp-server user a g v3 auth sha AuthPass priv aes 128 PrivPass", "snmp-server user a g v3 auth sha <hidden> priv aes 128 <hidden>"),
    ("/user add name=x password=hunter2", "/user add name=x password=<hidden>"),
    ("ntp server 10.255.0.1", "ntp server 10.255.0.1"),
])
def test_configuration_secrets_are_masked(line, shown):
    assert detail.mask(line) == shown


# ============================================================================ investigate
def test_investigation_drafts_from_the_evidence(sample, monkeypatch):
    monkeypatch.setenv("CONSOLE_AI_URL", "https://user:pw@models.example/v1")
    monkeypatch.setenv("CONSOLE_AI_MODEL", "qwen3:14b")
    for name in ("_zabbix", "_nautobot", "_glpi", "_golden"):
        monkeypatch.setattr(investigate, name, lambda *a, n=name: (f"{n} read", {"seen": n}, None))

    def unreachable(*args):
        raise RuntimeError("Semaphore timed out")
    monkeypatch.setattr(investigate, "_semaphore", unreachable)
    sent = []

    class Answer:
        def __init__(self, status, body):
            self.status_code, self._body, self.text = status, body, str(body)

        def json(self):
            return self._body

    class Http:
        def post(self, url, json, headers, timeout):
            sent.append((url, json, headers))
            if "response_format" in json:
                return Answer(400, {"error": "response_format not supported"})
            content = ('Here it is: {"cause": "The server lost power.", "confidence": "HIGH", "next_steps": ['
                       '{"text": "Collect the logs", "playbook": {"template": "Collect diagnostics", "devices": '
                       '["jkt01-srv01", "made-up-01"]}}, {"text": "Format it", "playbook": {"template": "Wipe"}}, '
                       '"Call the shop"], "note": "Power loss at the shop."}')
            return Answer(200, {"choices": [{"message": {"content": content}}]})
    monkeypatch.setattr(investigate, "make_session", lambda retries=0: Http())
    run = investigate.start(ENGINEER, "501")
    conforms(run, "Investigation")
    assert run["model"]["endpoint"] == "https://models.example/v1"        # no credentials shown
    deadline = data.time.time() + 10
    while investigate.get(run["id"])["status"] == "running" and data.time.time() < deadline:
        data.time.sleep(0.05)
    done = investigate.get(run["id"])
    conforms(done, "Investigation")
    assert done["status"] == "done" and done["confidence"] == "high" and done["cause"] == "The server lost power."
    steps = {s["id"]: s["status"] for s in done["steps"]}
    assert steps["semaphore"] == "failed" and steps["zabbix"] == "done"
    assert done["nextSteps"][0]["playbook"] == {"template": "Collect diagnostics", "devices": ["jkt01-srv01"]}
    assert "playbook" not in done["nextSteps"][1] and done["nextSteps"][2] == {"text": "Call the shop"}
    assert done["draftNote"].startswith("Power loss at the shop.")
    assert len(sent) == 2 and "response_format" not in sent[1][1] and sent[1][0] == "https://user:pw@models.example/v1/chat/completions"
    entry = store.entries(limit=1, action="investigate")[0]
    assert entry["outcome"] == "done" and entry["ticket"] == "Ticket #42"


def test_investigation_reports_a_model_that_does_not_answer(sample, monkeypatch):
    monkeypatch.setenv("CONSOLE_AI_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("CONSOLE_AI_MODEL", "llama3.1")
    for name in ("_zabbix", "_nautobot", "_glpi", "_golden", "_semaphore"):
        monkeypatch.setattr(investigate, name, lambda *a: ("read", {}, None))

    class Http:
        def post(self, *args, **kwargs):
            raise ConnectionError("connection refused")
    monkeypatch.setattr(investigate, "make_session", lambda retries=0: Http())
    run = investigate.start(ENGINEER, "501")
    deadline = data.time.time() + 10
    while investigate.get(run["id"])["status"] == "running" and data.time.time() < deadline:
        data.time.sleep(0.05)
    failed = investigate.get(run["id"])
    conforms(failed, "Investigation")
    assert failed["status"] == "failed" and "connection refused" in failed["error"]
    with pytest.raises(ActionError, match="resolved already"):
        investigate.start(ENGINEER, "999")
    with pytest.raises(actions.Forbidden):
        investigate.start(MANAGER, "501")


# ============================================================================= time zones
def test_glpi_times_follow_glpi_s_own_clock(monkeypatch):
    """The official GLPI image runs PHP in UTC: its "22:29:54" is 05:29:54 the next morning in Jakarta."""
    monkeypatch.setitem(data._offset, "value", 0)
    monkeypatch.setitem(data._offset, "at", data.time.time())
    assert data.glpi_time("2026-10-17 22:29:54").isoformat() == "2026-10-18T05:29:54+07:00"
    moment = data.glpi_time("2026-10-17 22:29:54")
    assert data.to_glpi(moment) == "2026-10-17 22:29:54"
    monkeypatch.setitem(data._offset, "value", 7 * 3600)          # GLPI set to the platform's zone
    assert data.glpi_time("2026-10-18 05:29:54").isoformat() == "2026-10-18T05:29:54+07:00"
    assert data.to_glpi(moment) == "2026-10-18 05:29:54"
    assert data.glpi_time("") is None and data.glpi_time("soon") is None


def test_glpi_clock_is_read_once_and_kept_when_glpi_is_down(monkeypatch):
    calls = []

    class Glpi:
        def __enter__(self):
            calls.append(1)
            if len(calls) > 1:
                raise ConnectionError("GLPI down")
            return self

        def __exit__(self, *exc):
            pass

        def request(self, method, path):
            clock = (data.datetime.now(data.timezone.utc) + data.timedelta(hours=7, seconds=3)).strftime(data.GLPI_FORMAT)
            return {"session": {"glpi_currenttime": clock}}
    monkeypatch.setattr(data.clients, "glpi", Glpi)
    monkeypatch.setitem(data._offset, "value", None)
    monkeypatch.setitem(data._offset, "at", 0.0)
    assert data.glpi_offset() == 7 * 3600 and data.glpi_offset() == 7 * 3600 and len(calls) == 1
    monkeypatch.setitem(data._offset, "at", 0.0)                   # an hour later, GLPI does not answer
    assert data.glpi_offset() == 7 * 3600 and len(calls) == 2      # the last answer stays


def test_downtime_follows_the_sla_service(monkeypatch):
    start = data.month_start()
    first = int(start.timestamp())

    class Zabbix:
        def call(self, method, params):
            assert method == "service.get" and params["filter"] == {"name": ["Customer acme"]}
            periods = params["selectStatusTimeline"]
            timeline = [{"start_value": "-1", "alarms": []} for _ in periods]
            timeline[0] = {"start_value": "-1", "alarms": [{"clock": str(first + 3600), "value": "4"},
                                                            {"clock": str(first + 5400), "value": "-1"}]}
            if len(periods) > 1:
                timeline[1] = {"start_value": "4", "alarms": [{"clock": str(periods[1]["period_from"] + 600),
                                                               "value": "-1"}]}
            return [{"serviceid": "2", "status_timeline": timeline}]
    monkeypatch.setattr(data.clients, "zabbix", Zabbix)
    data.cache.clear("downtime:acme")
    days = data.downtime_by_day("acme")
    assert days[0] == {"day": start.strftime("%Y-%m-%d"), "minutes": 30}
    if len(days) > 1:
        assert days[1]["minutes"] == 10 and all(d["minutes"] == 0 for d in days[2:])
    data.cache.clear("downtime:acme")
