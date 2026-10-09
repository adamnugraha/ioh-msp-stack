#!/usr/bin/env python3
"""Lab end-to-end checks. Each check prints PASS or FAIL with the evidence it found.

Run after lab/run-lab.sh (it calls this script). Takes about 15 minutes because it waits
for real Zabbix triggers and Semaphore runs.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(line.strip().split("=", 1) for line in open(ROOT / ".env") if "=" in line and not line.startswith("#"))
COMPOSE = ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "-f", str(ROOT / "lab/compose.ports.yaml")]
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(func):
        def run():
            try:
                ok, detail = func()
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            RESULTS.append((name, ok, detail))
            print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
            return ok
        run.check_name = func.__name__
        return run
    return wrap


# ------------------------------------------------------------------ API helpers
NB = "http://localhost:8081/api/"
NBH = {"Authorization": f"Token {ENV['NAUTOBOT_SUPERUSER_API_TOKEN']}", "Accept": "application/json",
       "Content-Type": "application/json"}


def nb_get(path, **params):
    response = requests.get(NB + path, headers=NBH, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def nb_list(path, **params):
    return nb_get(path, limit=500, **params)["results"]


def zbx(method, params):
    body = requests.post("http://localhost:8082/api_jsonrpc.php", timeout=30,
                         json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                         headers={"Authorization": "Bearer " + ENV["ZABBIX_API_TOKEN"]}).json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


class Glpi:
    base = "http://localhost:8083/apirest.php/"

    def __enter__(self):
        headers = {"Content-Type": "application/json", "App-Token": ENV["GLPI_APP_TOKEN"],
                   "Authorization": "user_token " + ENV["GLPI_USER_TOKEN"]}
        token = requests.get(self.base + "initSession", headers=headers, timeout=30).json()["session_token"]
        self.h = {"Content-Type": "application/json", "App-Token": ENV["GLPI_APP_TOKEN"], "Session-Token": token}
        requests.post(self.base + "changeActiveEntities", headers=self.h, json={"entities_id": 0, "is_recursive": True})
        return self

    def __exit__(self, *exc):
        requests.get(self.base + "killSession", headers=self.h, timeout=30)

    def get(self, path, **params):
        return requests.get(self.base + path, headers=self.h, params=params, timeout=30).json()

    def post(self, path, payload):
        return requests.post(self.base + path, headers=self.h, json={"input": payload}, timeout=30).json()

    def put(self, path, payload):
        return requests.put(self.base + path, headers=self.h, json={"input": payload}, timeout=30).json()


def glue(*args) -> subprocess.CompletedProcess:
    return subprocess.run(COMPOSE + ["exec", "-T", "msp-glue", "mspctl", *args], capture_output=True, text=True)


def tenant_cf(code):
    for t in nb_list("tenancy/tenants/"):
        if (t["custom_fields"] or {}).get("msp_code") == code:
            return t, t["custom_fields"]
    raise RuntimeError(f"tenant {code} not found")


def wait(predicate, timeout, interval=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


# ----------------------------------------------------------------------- checks
@check("All tools reachable from msp-glue")
def connectivity():
    result = glue("check")
    return result.returncode == 0, " | ".join(l.strip() for l in result.stdout.splitlines() if l.strip())


@check("Customers provisioned in every tool")
def tenants_provisioned():
    out = []
    for code in ("acme", "bankx"):
        _, cf = tenant_cf(code)
        ok = cf.get("msp_provisioning_status") == "provisioned" and all(
            cf.get(k) for k in ("msp_glpi_entity_id", "msp_zabbix_hostgroup_id", "msp_semaphore_project_id"))
        out.append((code, ok, cf.get("msp_provisioning_message")))
    return all(o[1] for o in out), str(out)


@check("Zabbix hosts created with customer groups, proxy groups and GLPI tags")
def zabbix_hosts():
    hosts = {h["host"]: h for h in zbx("host.get", {
        "output": ["host", "monitored_by", "proxy_groupid"], "selectTags": ["tag", "value"],
        "selectHostGroups": ["name"], "tags": [{"tag": "managed_by", "value": "msp-glue", "operator": 1}]})}
    groups = {g["proxy_groupid"]: g["name"] for g in zbx("proxygroup.get", {"output": ["proxy_groupid", "name"]})}
    want = {"acme-jkt01-rtr01": "pg-central", "acme-jkt01-srv01": "pg-central", "bankx-ckr01-rtr01": "pg-bankx"}
    problems = []
    for name, pg in want.items():
        host = hosts.get(name)
        if not host:
            problems.append(f"{name} missing")
            continue
        tags = {t["tag"]: t["value"] for t in host["tags"]}
        if groups.get(host["proxy_groupid"]) != pg:
            problems.append(f"{name} proxy group {groups.get(host['proxy_groupid'])}")
        if not tags.get("glpi_asset_id") or not tags.get("glpi_entity_id"):
            problems.append(f"{name} without GLPI tags")
    return not problems, problems or f"{len(want)} hosts correct"


@check("GLPI assets in each customer's entity")
def glpi_assets():
    _, acme = tenant_cf("acme")
    _, bankx = tenant_cf("bankx")
    with Glpi() as g:
        net = g.get("NetworkEquipment", range="0-100")
        comp = g.get("Computer", range="0-100")
    where = {a["name"]: a["entities_id"] for a in net + comp}
    expected = {"jkt01-rtr01": acme["msp_glpi_entity_id"], "jkt01-srv01": acme["msp_glpi_entity_id"],
                "bdg01-sw01": acme["msp_glpi_entity_id"], "ckr01-rtr01": bankx["msp_glpi_entity_id"]}
    wrong = {k: where.get(k) for k, v in expected.items() if where.get(k) != v}
    return not wrong, wrong or "4 assets in the right entities (including a Planned switch)"


@check("Customer isolation in Zabbix permissions")
def zabbix_permissions():
    groups = {g["name"]: g for g in zbx("usergroup.get", {"output": ["usrgrpid", "name"],
                                                          "selectHostGroupRights": "extend"})}
    names = {g["groupid"]: g["name"] for g in zbx("hostgroup.get", {"output": ["groupid", "name"]})}
    acme_rights = {names[r["id"]] for r in groups["Customer acme read-only"]["hostgroup_rights"]}
    leak = [n for n in acme_rights if "Bank Contoh" in n]
    return not leak, f"acme read-only group sees {sorted(acme_rights)}"


@check("New device in Nautobot reaches Zabbix and GLPI through the webhook")
def webhook_device():
    tenant, _ = tenant_cf("acme")
    def first(path, **k):
        return nb_list(path, **k)[0]
    site = first("dcim/locations/", name="ACME HQ Jakarta")
    payload = {"name": "jkt01-test01", "tenant": tenant["id"], "location": site["id"],
               "role": first("extras/roles/", name="switch")["id"], "platform": first("dcim/platforms/", name="Cisco IOS")["id"],
               "device_type": first("dcim/device-types/", model="C9200L-24T-4G")["id"],
               "status": first("extras/statuses/", name="Active")["id"]}
    device = requests.post(NB + "dcim/devices/", headers=NBH, json=payload, timeout=30).json()
    iface = requests.post(NB + "dcim/interfaces/", headers=NBH, timeout=30, json={
        "device": device["id"], "name": "Vlan99", "type": "virtual", "status": payload["status"]}).json()
    subnet = ipaddress.ip_network(subprocess.check_output(
        ["docker", "network", "inspect", "msp", "-f", "{{(index .IPAM.Config 0).Subnet}}"], text=True).strip())
    test_address = f"{subnet.network_address + subnet.num_addresses - 6}/{subnet.prefixlen}"  # unused, near the end
    ip = requests.post(NB + "ipam/ip-addresses/", headers=NBH, timeout=30, json={
        "address": test_address, "namespace": first("ipam/namespaces/", name="Global")["id"],
        "status": payload["status"], "type": "host"}).json()
    requests.post(NB + "ipam/ip-address-to-interface/", headers=NBH, json={"ip_address": ip["id"], "interface": iface["id"]})
    started = time.time()
    requests.patch(NB + f"dcim/devices/{device['id']}/", headers=NBH, json={"primary_ip4": ip["id"]})
    host = wait(lambda: zbx("host.get", {"filter": {"host": ["acme-jkt01-test01"]}, "output": ["hostid", "status"]}), 60, 2)
    seconds = time.time() - started
    requests.delete(NB + f"dcim/devices/{device['id']}/", headers=NBH)
    requests.delete(NB + f"ipam/ip-addresses/{ip['id']}/", headers=NBH)
    disabled = wait(lambda: zbx("host.get", {"filter": {"host": ["acme-jkt01-test01"], "status": 1}, "output": ["hostid"]}), 60, 2)
    return bool(host and disabled), f"host created after {seconds:.0f}s, disabled after deletion: {bool(disabled)}"


@check("Problem in Zabbix opens, updates and solves a GLPI incident")
def alert_to_ticket():
    # Take the simulated router off the network (a paused container still answers ping), and put it
    # back on the same address afterwards so Zabbix sees it recover.
    container = "msp-lab-devices-lab-acme-rtr01-1"
    address = subprocess.check_output(
        ["docker", "inspect", "-f", "{{.NetworkSettings.Networks.msp.IPAddress}}", container], text=True).strip()
    subprocess.run(["docker", "network", "disconnect", "msp", container], check=True, capture_output=True)
    try:
        def ticket_open():
            with Glpi() as g:
                items = g.get("Ticket", range="0-50", sort="id", order="DESC", **{"searchText[name]": "jkt01-rtr01"})
            return [t for t in items if "ICMP" in t["name"] and int(t["status"]) < 5]
        found = wait(ticket_open, 600, 15)
        if not found:
            return False, "no ticket within 10 minutes"
        ticket = found[0]
        with Glpi() as g:
            links = g.get(f"Ticket/{ticket['id']}/Item_Ticket")
        evidence = f"ticket #{ticket['id']} '{ticket['name']}' entity {ticket['entities_id']} linked {[(l['itemtype'], l['items_id']) for l in links]}"
    finally:
        subprocess.run(["docker", "network", "connect", "--ip", address, "msp", container], check=True,
                       capture_output=True)

    def solved():
        with Glpi() as g:
            return int(g.get(f"Ticket/{ticket['id']}")["status"]) >= 5
    ok = wait(solved, 600, 15)
    # The outage must also count as downtime in the customer's monthly availability report.
    sla = zbx("sla.get", {"filter": {"name": ["Customer acme availability"]}, "output": ["slaid", "slo"]})
    downtime = 0
    if sla:
        sli = zbx("sla.getsli", {"slaid": sla[0]["slaid"], "periods": 1})
        downtime = sum(int(row[0]["downtime"]) for row in sli["sli"])
    return bool(ok and links and downtime > 0), evidence + f", solved after recovery: {bool(ok)}, " \
        f"downtime counted in the acme availability report: {downtime} s"


@check("Approved GLPI change runs a Semaphore playbook and reports back")
def change_automation():
    _, acme = tenant_cf("acme")
    with Glpi() as g:
        cats = g.get("ITILCategory", range="0-100")
        cat = [c for c in cats if c["completename"].endswith("Standard change > Collect diagnostics")][0]
        change = g.post("Change", {"name": "Lab check: collect diagnostics", "entities_id": acme["msp_glpi_entity_id"],
                                   "itilcategories_id": cat["id"],
                                   "content": "<p>automation:<br>device: jkt01-srv01</p>"})
        g.put(f"Change/{change['id']}", {"id": change["id"], "status": 7})

    def finished():
        with Glpi() as g:
            notes = g.get(f"Change/{change['id']}/ITILFollowup")
        done = [n for n in notes if "finished with status" in (n.get("content") or "")]
        return done
    notes = wait(finished, 600, 15)
    if not notes:
        return False, f"change #{change['id']}: no result follow-up within 10 minutes"
    text = notes[-1]["content"]
    return "<b>success</b>" in text and "jkt01-rtr01" not in text.split("PLAY RECAP")[-1], \
        f"change #{change['id']}: {text[:120]}"


@check("Golden Config backs up, renders and checks each customer separately")
def golden_config():
    job = nb_list("extras/jobs/", module_name="nautobot_golden_config.jobs", job_class_name="AllDevicesGoldenConfig")[0]
    statuses = {}
    for code in ("acme", "bankx"):
        tenant, _ = tenant_cf(code)
        run = requests.post(NB + f"extras/jobs/{job['id']}/run/", headers=NBH, timeout=30, json={
            "data": {"tenant": [tenant["id"]], "fail_job_on_task_failure": True}}).json()
        result_id = run["job_result"]["id"]
        def done():
            result = nb_get(f"extras/job-results/{result_id}/")
            return result if result["status"]["value"] in ("SUCCESS", "FAILURE") else None
        statuses[code] = (wait(done, 600, 5) or {}).get("status", {}).get("value")
    headers = {"Authorization": "token " + ENV["FORGEJO_API_TOKEN"]}
    backup = requests.get("http://localhost:8085/api/v1/repos/msp/backups-acme/contents/acme-hq-jakarta/jkt01-rtr01.cfg",
                          headers=headers, timeout=30)
    text = base64.b64decode(backup.json()["content"]).decode() if backup.status_code == 200 else ""
    redacted = "<redacted>" in text and "S3cretCommunity" not in text
    drift = [c for c in nb_list("plugins/golden-config/config-compliance/", depth=1)
             if c["device"]["name"] == "jkt01-rtr01" and not c["compliance"]]
    ok = all(v == "SUCCESS" for v in statuses.values()) and redacted and drift
    return bool(ok), f"jobs {statuses}, backup stored with secrets redacted: {redacted}, drift on jkt01-rtr01: " \
                     f"{[d['rule']['display'] for d in drift]}"


@check("Configuration drift becomes a GLPI ticket in the customer's entity")
def drift_ticket():
    result = glue("compliance")
    with Glpi() as g:
        tickets = g.get("Ticket", range="0-50", **{"searchText[name]": "Configuration drift on jkt01-rtr01"})
    return bool(tickets), f"{result.stdout.strip()[-80:]} -> {[(t['id'], t['entities_id']) for t in tickets]}"


@check("Customer portal user sees only its own customer's tickets")
def customer_portal_user():
    login = f"lab.viewer.{int(time.time())}@acme.example"   # a new account on every run
    result = glue("user", "add", "acme", login, "--name", "Lab Viewer", "--grafana")
    passwords = [line.split("one-time password: ")[1].split(",")[0].strip()
                 for line in result.stdout.splitlines() if "one-time password: " in line]
    if result.returncode != 0 or not passwords:
        return False, f"user add failed or account already existed: {result.stdout[-300:]} {result.stderr[-300:]}"
    _, acme = tenant_cf("acme")
    _, bankx = tenant_cf("bankx")
    # The customer opens a session with its own name and password, as the portal does.
    auth = base64.b64encode(f"{login}:{passwords[0]}".encode()).decode()
    h = {"Content-Type": "application/json", "App-Token": ENV["GLPI_APP_TOKEN"], "Authorization": "Basic " + auth}
    session = requests.get(Glpi.base + "initSession", headers=h, timeout=30).json()["session_token"]
    hs = {"Content-Type": "application/json", "App-Token": ENV["GLPI_APP_TOKEN"], "Session-Token": session}
    profiles = requests.get(Glpi.base + "getMyProfiles", headers=hs, timeout=30).json().get("myprofiles", [])
    entities = requests.get(Glpi.base + "getMyEntities", headers=hs, params={"is_recursive": 1},
                            timeout=30).json().get("myentities", [])
    requests.get(Glpi.base + "killSession", headers=hs, timeout=30)
    entity_ids = sorted(int(e["id"]) for e in entities)
    ok = entity_ids == [int(acme["msp_glpi_entity_id"])] and [p["name"] for p in profiles] == ["Self-Service"]
    ok = ok and int(bankx["msp_glpi_entity_id"]) not in entity_ids
    detail = f"GLPI profiles {[p['name'] for p in profiles]}, entities {entity_ids} (acme is {acme['msp_glpi_entity_id']})"
    if ENV.get("GRAFANA_URL"):
        auth = ("admin", ENV["GRAFANA_ADMIN_PASSWORD"])
        user = requests.get("http://localhost:8087/api/users/lookup", params={"loginOrEmail": login}, auth=auth,
                            timeout=30).json()
        orgs = requests.get(f"http://localhost:8087/api/users/{user['id']}/orgs", auth=auth, timeout=30).json()
        names = [(o["name"], o["role"]) for o in orgs]
        ok = ok and names == [("PT ACME Retail Indonesia", "Viewer")]
        detail += f"; Grafana organisations {names}"
    return ok, detail


@check("Customer dashboards only see their own hosts (Grafana, phase 3)")
def grafana_isolation():
    if not ENV.get("GRAFANA_URL"):
        return True, "skipped (GRAFANA_URL not set)"
    auth = ("admin", ENV["GRAFANA_ADMIN_PASSWORD"])
    seen = {}
    for org in requests.get("http://localhost:8087/api/orgs", auth=auth, timeout=30).json():
        if org["name"] not in ("PT ACME Retail Indonesia", "PT Bank Contoh Tbk"):
            continue
        h = {"X-Grafana-Org-Id": str(org["id"])}
        ds = requests.get("http://localhost:8087/api/datasources/name/Zabbix", auth=auth, headers=h, timeout=30).json()
        r = requests.post(f"http://localhost:8087/api/datasources/uid/{ds['uid']}/resources/zabbix-api", auth=auth,
                          headers=h, timeout=30, json={"datasourceId": ds["id"], "method": "host.get",
                                                       "params": {"output": ["host"]}})
        seen[org["name"]] = sorted(x["host"] for x in r.json().get("result", []))
    leak = [h for h in seen.get("PT ACME Retail Indonesia", []) if h.startswith("bankx")] + \
           [h for h in seen.get("PT Bank Contoh Tbk", []) if h.startswith("acme")]
    return bool(seen) and not leak, json.dumps(seen)


ALL = (connectivity, tenants_provisioned, zabbix_hosts, glpi_assets, zabbix_permissions, webhook_device,
       golden_config, drift_ticket, change_automation, customer_portal_user, alert_to_ticket, grafana_isolation)

if __name__ == "__main__":
    # python3 lab/check.py                       run every check
    # python3 lab/check.py golden_config ...     run only the named checks
    wanted = sys.argv[1:]
    for test in ALL:
        if not wanted or test.check_name in wanted:
            test()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed.")
    sys.exit(1 if failed else 0)
