#!/usr/bin/env python3
"""Check the path test templates and msp-glue's path test sync against a real Zabbix 7.0.

Start the bench first (lab/compose.paths-ci.yaml), then:  python3 lab/check_paths.py

It registers the bench's collector in a proxy group, lets msp-glue create path test hosts for a made-up
customer, and waits until every check returns what it should: ping, a TCP connection, a web request, and a
target that never answers (whose problem must open). It ends with the sync run twice more: once unchanged,
once with a target removed. Exit status 0 when everything matched.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ZABBIX_URL", "http://127.0.0.1:8082")
os.environ.setdefault("GLUE_MAPPINGS", str(ROOT / "glue" / "mappings.yaml"))
sys.path.insert(0, str(ROOT / "glue"))

from msp_glue import paths  # noqa: E402
from msp_glue.clients.zabbix import Zabbix  # noqa: E402
from msp_glue.common import Tenant  # noqa: E402

URL = os.environ["ZABBIX_URL"]
TARGETS = [
    {"name": "Zabbix server", "address": "zabbix-server", "segment": "ioh", "checks": ["icmp"], "rtt_ms": 500},
    {"name": "Database port", "address": "postgres", "segment": "internet", "checks": ["tcp"], "port": 5432},
    {"name": "Zabbix web", "url": "http://zabbix-web:8080/", "segment": "application", "checks": ["https", "icmp"]},
    {"name": "Nobody home", "address": "192.0.2.10", "segment": "application", "checks": ["icmp"]},
]


def say(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def wait(what: str, check, limit: int = 600, step: int = 10):
    say(f"waiting for {what}")
    started = time.time()
    last_error = None
    while time.time() - started < limit:
        try:
            result = check()
            if result:
                say(f"  {what}: ok after {int(time.time() - started)} s")
                return result
        except Exception as exc:  # noqa: BLE001 - keep waiting, report the last reason
            last_error = exc
        time.sleep(step)
    raise SystemExit(f"FAILED: {what} not reached within {limit} s (last error: {last_error})")


def tenant(targets: list[dict]) -> Tenant:
    return Tenant.from_nautobot({"id": "ci", "name": "CI Customer", "custom_fields": {
        "msp_code": "ci", "msp_proxy_group": "pg-ci", "msp_path_targets": {"defaults": False, "targets": targets}}})


def items_by_host(z: Zabbix, hostids: list[str]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for item in z.call("item.get", {"hostids": hostids, "output": ["hostid", "key_", "lastvalue", "lastclock", "state",
                                                                   "error"]}):
        out.setdefault(item["hostid"], {})[item["key_"]] = item
    return out


def main() -> int:
    wait("the Zabbix API", lambda: Zabbix(URL, "").version(), limit=600)
    z = wait("sign-in", lambda: Zabbix.login(URL, "Admin", "zabbix"), limit=300)
    say(f"Zabbix {z.version()}")
    group = z.ensure_proxy_group("pg-ci")
    z.ensure_active_proxy("ci-collector", proxy_groupid=group, local_address="ci-collector")

    customer = tenant(TARGETS)
    report = paths.sync_tenant(customer, z)
    say(f"first sync: {report.summary()}")
    if report.errors or len(report.created) != len(TARGETS):
        raise SystemExit(f"FAILED: expected {len(TARGETS)} hosts without errors, got {report.created} {report.errors}")
    versions = {t["host"]: t["vendor_version"] for t in z.call("template.get", {
        "filter": {"host": list(paths.CHECK_TEMPLATES.values())}, "output": ["host", "vendor_version"]})}
    say(f"templates: {versions}")

    wait("the collector to come online", lambda: str(z.call("proxy.get", {
        "filter": {"name": ["ci-collector"]}, "output": ["state"]})[0]["state"]) == "2", limit=300)

    hosts = {h["host"]: h for h in paths.managed_path_hosts(z, "ci")}
    by_target = {next(t["value"] for t in h["tags"] if t["tag"] == "target"): h["hostid"] for h in hosts.values()}

    def values():
        items = items_by_host(z, list(by_target.values()))
        broken = [f"{k}: {i['error']}" for v in items.values() for k, i in v.items() if str(i["state"]) == "1"]
        if broken:
            raise SystemExit("FAILED: Zabbix cannot run these checks: " + "; ".join(broken))
        need = {
            ("Zabbix server", "icmppingloss[,10]"), ("Zabbix server", "icmppingsec[,10]"),
            ("Database port", "net.tcp.service.perf[tcp,,{$PATH.PORT}]"),
            ("Zabbix web", "msp.path.https.ok"), ("Zabbix web", "msp.path.https.status"),
            ("Zabbix web", "msp.path.https.time"), ("Zabbix web", "icmppingloss[,10]"),
            ("Nobody home", "icmppingloss[,10]"),
        }
        got = {}
        for target, key in need:
            item = items.get(by_target[target], {}).get(key)
            if not item or int(item.get("lastclock") or 0) == 0:
                return None
            got[(target, key)] = item["lastvalue"]
        return got

    got = wait("a value from every check", values, limit=600)
    for (target, key), value in sorted(got.items()):
        say(f"  {target:<14} {key:<44} {value}")
    expect = [
        (float(got[("Zabbix server", "icmppingloss[,10]")]) == 0, "ping to the Zabbix server loses nothing"),
        (float(got[("Zabbix server", "icmppingsec[,10]")]) > 0, "ping to the Zabbix server has a delay"),
        (float(got[("Database port", "net.tcp.service.perf[tcp,,{$PATH.PORT}]")]) > 0, "the database port answers"),
        (got[("Zabbix web", "msp.path.https.ok")] == "1", "the web request is answered"),
        (got[("Zabbix web", "msp.path.https.status")] == "200", "the web request returns 200"),
        (float(got[("Nobody home", "icmppingloss[,10]")]) == 100, "the address that does not exist loses everything"),
    ]
    failed = [text for ok, text in expect if not ok]
    if failed:
        raise SystemExit("FAILED: " + "; ".join(failed))

    def outage_problem():
        found = z.call("problem.get", {"output": ["name", "severity"], "selectTags": "extend",
                                       "tags": [{"tag": "managed_by", "value": paths.MANAGED_VALUE, "operator": 1},
                                                {"tag": "target", "value": "Nobody home", "operator": 1}]})
        return [p for p in found if "does not answer" in p["name"]]

    problems = wait("the problem for the target that never answers", outage_problem, limit=420)
    tags = {t["tag"]: t["value"] for t in problems[0]["tags"]}
    say(f"  problem: {problems[0]['name']} (severity {problems[0]['severity']}, tags {tags})")
    if tags.get("scope") != "path" or tags.get("segment") != "application" or tags.get("tenant") != "ci":
        raise SystemExit(f"FAILED: the problem lacks the scope, segment or tenant tag: {tags}")

    again = paths.sync_tenant(customer, z)
    if again.created or again.errors:
        raise SystemExit(f"FAILED: a second sync should only repair: {again.summary()} {again.errors}")
    fewer = paths.sync_tenant(tenant(TARGETS[:-1]), z)
    if fewer.disabled != ["ci/ci-collector/Nobody home"]:
        raise SystemExit(f"FAILED: removing a target should disable its host: {fewer.summary()} {fewer.disabled}")
    say("path tests: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
