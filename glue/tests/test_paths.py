"""Path tests from the site collectors: target parsing and the Zabbix hosts msp-glue keeps (kit 1.6.0).

Run:  cd glue && python -m pytest -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("GLUE_MAPPINGS", str(Path(__file__).resolve().parent.parent / "mappings.yaml"))

from msp_glue import paths  # noqa: E402
from msp_glue.common import Tenant  # noqa: E402

TEMPLATES = Path(__file__).resolve().parents[2] / "zabbix" / "templates" / "msp_path_tests.yaml"


def tenant(**cf) -> Tenant:
    return Tenant.from_nautobot({"id": "t1", "name": "PT ACME Retail Indonesia",
                                 "custom_fields": {"msp_code": "acme", "msp_proxy_group": "pg-acme", **cf}})


# ------------------------------------------------------------------------------- targets
def test_target_defaults_follow_what_is_given():
    t = paths.parse_target({"name": "IOH edge", "address": "10.20.30.1", "segment": "edge"})
    assert (t.segment, t.checks, t.port) == ("ioh", ["icmp"], 443)
    web = paths.parse_target({"name": "Core banking", "url": "https://core.example.co.id:8443/health"})
    assert (web.segment, web.checks, web.address, web.port) == ("application", ["https"], "core.example.co.id", 8443)


@pytest.mark.parametrize("raw, message", [
    ({"address": "10.0.0.1"}, "no name"),
    ({"name": "x", "segment": "moon", "address": "10.0.0.1"}, "segment"),
    ({"name": "x"}, "address"),
    ({"name": "x", "address": "10.0.0.1", "checks": ["https"]}, "needs a url"),
    ({"name": "x", "address": "10.0.0.1", "checks": ["dns"]}, "checks"),
    ({"name": "x", "url": "ftp://files.example.com"}, "https://"),
    ({"name": "x", "address": "10.0.0.1; rm -rf /"}, "not an IP"),
    ({"name": "x", "address": "10.0.0.1", "port": 70000, "checks": ["tcp"]}, "between 1 and 65535"),
])
def test_bad_targets_say_what_is_wrong(raw, message):
    with pytest.raises(ValueError, match=message):
        paths.parse_target(raw)


def test_limits_come_from_the_target_then_the_mapping():
    t = paths.parse_target({"name": "x", "address": "10.0.0.1", "rtt_ms": 40}, {"rtt_ms": 150, "loss_percent": 2})
    assert (t.rtt_ms, t.loss_percent, t.http_ms) == (40, 2, 2000)
    assert paths.parse_target({"name": "x", "address": "10.0.0.1", "rtt_ms": "fast"}).rtt_ms == 150


def test_customer_targets_add_to_and_replace_defaults():
    targets, errors = paths.tenant_targets(tenant(msp_path_targets=[
        {"name": "IOH edge router", "address": "10.20.30.1", "segment": "ioh"},
        {"name": "Microsoft 365", "url": "https://outlook.office.com/", "http_ms": 3000},
        {"name": "broken"},
    ]))
    names = [t.name for t in targets]
    assert "IOH edge router" in names and "Internet reference (Cloudflare)" in names
    m365 = next(t for t in targets if t.name == "Microsoft 365")
    assert m365.url == "https://outlook.office.com/" and m365.http_ms == 3000     # the customer's entry wins
    assert names.count("Microsoft 365") == 1
    assert len(errors) == 1 and "broken" in errors[0]


def test_customer_can_leave_out_the_defaults_and_bad_json_is_reported():
    targets, _ = paths.tenant_targets(tenant(msp_path_targets={"defaults": False, "targets": [
        {"name": "IOH edge router", "address": "10.20.30.1", "segment": "ioh"}]}))
    assert [t.name for t in targets] == ["IOH edge router"]
    targets, errors = paths.tenant_targets(tenant(msp_path_targets="[{oops"))
    assert errors and "not valid JSON" in errors[0]
    assert targets      # the defaults still apply


# ----------------------------------------------------------------------------- templates
def test_template_file_is_valid_and_versioned():
    data = yaml.safe_load(TEMPLATES.read_text())["zabbix_export"]
    assert data["version"] == "7.0"
    names = {t["template"] for t in data["templates"]}
    assert names == set(paths.CHECK_TEMPLATES.values())
    assert paths.template_version(TEMPLATES.read_text()) == (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
    uuids = []
    for tpl in data["templates"]:
        uuids.append(tpl["uuid"])
        keys = {i["key"] for i in tpl.get("items", [])}
        for item in tpl.get("items", []):
            uuids.append(item["uuid"])
            if item["type"] == "DEPENDENT":
                assert item["master_item"]["key"] in keys
            for trig in item.get("triggers", []):
                uuids.append(trig["uuid"])
                # every trigger reads only items of its own template
                assert f"/{tpl['template']}/{item['key']}" in trig["expression"]
                assert trig["priority"] in ("INFO", "WARNING", "AVERAGE")
                assert {"tag": "scope", "value": "path"} in trig["tags"]
                for dep in trig.get("dependencies", []):
                    assert any(t["name"] == dep["name"] and t["expression"] == dep["expression"]
                               for i in tpl["items"] for t in i.get("triggers", []))
    # Zabbix accepts only version 4 UUIDs, written as 32 hexadecimal characters
    assert len(uuids) == len(set(uuids))
    assert all(len(u) == 32 and u[12] == "4" and u[16] in "89ab" for u in uuids)


# -------------------------------------------------------------------------------- hosts
class FakeZabbix:
    def __init__(self, proxies=None, hosts=None, templates=None):
        self.proxies = proxies if proxies is not None else [
            {"proxyid": "11", "name": "acme-jkt-01", "state": "2"}, {"proxyid": "12", "name": "acme-sby-01", "state": "1"}]
        self.hosts = hosts or []
        self.templates = templates if templates is not None else [
            {"templateid": str(100 + i), "host": name, "vendor_version": paths.template_version(TEMPLATES.read_text())}
            for i, name in enumerate(paths.CHECK_TEMPLATES.values())]
        self.created, self.updated, self.calls = [], [], []

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "proxy.get":
            return self.proxies
        if method == "host.get":
            return self.hosts
        if method == "template.get":
            return self.templates
        if method == "configuration.import":
            return True
        return []

    def proxy_group_id(self, name):
        return "7" if name == "pg-acme" else None

    def template_ids(self, names):
        return {t["host"]: t["templateid"] for t in self.templates if t["host"] in names}

    def ensure_hostgroup(self, name):
        return {"Customers/PT ACME Retail Indonesia": "50"}.get(name, "51")

    def hostgroups_with_prefix(self, prefix):
        return [{"groupid": "50", "name": prefix}]

    def ensure_usergroup(self, *args, **kwargs):
        return "1"

    def host_create(self, params):
        self.created.append(params)
        return str(900 + len(self.created))

    def host_update(self, params):
        self.updated.append(params)

    def interface_update(self, params):
        self.calls.append(("hostinterface.update", params))


def _macro(params, name):
    return next(m["value"] for m in params["macros"] if m["macro"] == name)


def test_one_host_per_collector_and_target(monkeypatch):
    monkeypatch.setattr("msp_glue.tenants.managed_tenants", lambda nb=None: [])
    z = FakeZabbix()
    t = tenant(msp_path_targets=[{"name": "IOH edge Jakarta", "address": "10.20.30.1", "segment": "ioh",
                                  "collector": "acme-jkt-01"}])
    report = paths.sync_tenant(t, z)
    assert not report.errors
    # 4 defaults on each of 2 collectors, plus the Jakarta-only edge target
    assert len(z.created) == 9
    edge = next(p for p in z.created if _macro(p, "{$PATH.NAME}") == "IOH edge Jakarta")
    assert edge["host"] == "acme-path-acme-jkt-01-ioh-edge-jakarta"
    assert edge["monitored_by"] == 1 and edge["proxyid"] == "11"
    assert edge["interfaces"][0]["ip"] == "10.20.30.1" and edge["interfaces"][0]["useip"] == 1
    assert {"tag": "managed_by", "value": "msp-glue-paths"} in edge["tags"]
    assert {"tag": "segment", "value": "ioh"} in edge["tags"]
    assert [t["templateid"] for t in edge["templates"]] == ["100"]          # ICMP only
    web = next(p for p in z.created if _macro(p, "{$PATH.NAME}") == "Microsoft 365")
    assert web["interfaces"][0]["dns"] == "outlook.office365.com" and web["interfaces"][0]["useip"] == 0
    assert _macro(web, "{$PATH.URL}") == "https://outlook.office365.com/"
    assert _macro(web, "{$PATH.HTTP.WARN}") == "2" and _macro(web, "{$PATH.RTT.WARN}") == "0.15"
    assert [t["templateid"] for t in web["templates"]] == ["102"]           # HTTPS only


def test_hosts_are_repaired_and_unwanted_ones_disabled(monkeypatch):
    monkeypatch.setattr("msp_glue.tenants.managed_tenants", lambda nb=None: [])
    existing = {"hostid": "5", "host": "acme-path-acme-jkt-01-microsoft-365", "name": "old", "status": "0",
                "tags": [{"tag": "collector", "value": "acme-jkt-01"}, {"tag": "path_target", "value": "Microsoft 365"}],
                "parentTemplates": [{"templateid": "100", "host": "MSP path test ICMP"}, {"templateid": "300", "host": "own"}],
                "interfaces": [{"interfaceid": "8", "type": "1", "main": "1", "ip": "1.2.3.4", "dns": "", "useip": "1"}]}
    gone = {"hostid": "6", "host": "acme-path-acme-jkt-01-old-target", "name": "old", "status": "0",
            "tags": [{"tag": "collector", "value": "acme-jkt-01"}, {"tag": "path_target", "value": "Old target"}]}
    z = FakeZabbix(proxies=[{"proxyid": "11", "name": "acme-jkt-01", "state": "2"}], hosts=[existing, gone])
    report = paths.sync_tenant(tenant(), z)
    update = next(u for u in z.updated if u["hostid"] == "5")
    # the HTTPS template replaces the ICMP one, and a template linked by hand stays
    assert [t["templateid"] for t in update["templates"]] == ["102", "300"]
    assert update["templates_clear"] == [{"templateid": "100"}]
    assert ("hostinterface.update", {"interfaceid": "8", "ip": "", "dns": "outlook.office365.com", "useip": 0}) in z.calls
    retired = next(u for u in z.updated if u["hostid"] == "6")
    assert retired["status"] == 1 and {"tag": "msp_retired", "value": "target or collector removed"} in retired["tags"]
    assert report.disabled == ["acme/acme-jkt-01/Old target"]


def test_no_collector_means_no_path_tests():
    z = FakeZabbix()
    report = paths.sync_tenant(tenant(msp_proxy_group=""), z)
    assert not z.created and not report.errors


def test_old_templates_are_imported_again(monkeypatch):
    monkeypatch.setattr("msp_glue.tenants.managed_tenants", lambda nb=None: [])
    z = FakeZabbix(templates=[{"templateid": "100", "host": "MSP path test ICMP", "vendor_version": "1.5.9"}])
    paths.ensure_templates(z)
    imports = [p for m, p in z.calls if m == "configuration.import"]
    assert imports and imports[0]["rules"]["items"]["deleteMissing"] is True
