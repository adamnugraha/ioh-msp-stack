"""Unit tests for msp-glue logic that does not need the real tools.

Run:  cd glue && python -m pytest -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("GLUE_MAPPINGS", str(Path(__file__).resolve().parent.parent / "mappings.yaml"))

from msp_glue import changes, server  # noqa: E402
from msp_glue.common import Tenant, host_ip, zabbix_safe  # noqa: E402
from msp_glue.inventory import render_inventory  # noqa: E402
from msp_glue.clients.nautobot import _rename_custom_fields  # noqa: E402


def tenant(**kw) -> Tenant:
    raw = {"id": "t1", "name": "PT ACME Retail Indonesia",
           "custom_fields": {"msp_code": "acme", "msp_service_tier": "silver", **kw}}
    return Tenant.from_nautobot(raw)


# ----------------------------------------------------------------------------- tenants
def test_tenant_code_validation():
    assert tenant().valid
    assert tenant(msp_code="ACME").code == "acme"      # typed in capitals: stored and used in lowercase
    assert tenant(msp_code=" acme ").valid             # stray spaces are removed
    assert not tenant(msp_code="a").valid              # too short
    assert not tenant(msp_code="acme_retail").valid    # underscore not allowed
    assert tenant(msp_code="bank-contoh").valid


def test_tenant_names_and_proxy_group():
    t = tenant()
    assert t.zabbix_group == "Customers/PT ACME Retail Indonesia"
    assert t.macro_template == "Customer acme settings"
    assert t.proxy_group_name == "pg-central"
    assert tenant(msp_proxy_group="pg-acme").proxy_group_name == "pg-acme"


# ------------------------------------------------------------------------------- helpers
def test_host_ip_strips_prefix_length():
    assert host_ip({"host": "10.1.2.3"}) == "10.1.2.3"
    assert host_ip({"address": "10.1.2.3/24"}) == "10.1.2.3"
    assert host_ip(None) == ""


def test_zabbix_safe_names():
    assert zabbix_safe("acme-jkt01/rtr01") == "acme-jkt01_rtr01"
    assert len(zabbix_safe("x" * 300)) == 128


def test_graphql_custom_fields_are_renamed():
    data = {"devices": [{"name": "a", "_custom_field_data": {"x": 1}, "tenant": {"_custom_field_data": {}}}]}
    out = _rename_custom_fields(data)
    assert out["devices"][0]["custom_fields"] == {"x": 1}
    assert "custom_fields" in out["devices"][0]["tenant"]


# ----------------------------------------------------------------------------- inventory
def test_inventory_groups_by_role_and_skips_inactive():
    devices = [
        {"id": "1", "name": "rtr01", "status": {"name": "Active"}, "role": {"name": "router"},
         "platform": {"network_driver": "cisco_xe"}, "location": {"name": "HQ"}, "primary_ip4": {"host": "10.0.0.1"}},
        {"id": "2", "name": "srv01", "status": {"name": "Active"}, "role": {"name": "server"},
         "platform": {"network_driver": "linux"}, "location": {"name": "HQ"}, "primary_ip4": {"host": "10.0.0.2"}},
        {"id": "3", "name": "sw01", "status": {"name": "Planned"}, "role": {"name": "switch"},
         "platform": {"network_driver": "cisco_ios"}, "location": {"name": "HQ"}, "primary_ip4": {"host": "10.0.0.3"}},
    ]
    text = render_inventory(tenant(), devices)
    assert "rtr01" in text and "srv01" in text and "sw01" not in text
    assert "cisco.ios.ios" in text
    assert "ansible_connection: ssh" in text


# ------------------------------------------------------------------------------- changes
@pytest.mark.parametrize("content,expected", [
    ("<p>Please bounce.</p><p>automation:<br>device: jkt01-rtr01<br>interface: Gi0/2</p>",
     {"device": "jkt01-rtr01", "interface": "Gi0/2"}),
    ("automation:\ndevice: srv01\n\nThanks", {"device": "srv01"}),
    ("<p>No variables here</p>", {}),
    ("<p>automation:</p><p>device: a&amp;b</p>", {"device": "a&b"}),
])
def test_change_variables(content, expected):
    assert changes.parse_variables(content) == expected


# ------------------------------------------------------------------------------ webhooks
def _event(pre_cf, post_cf, **extra_post):
    pre = {"id": "d1", "name": "rtr01", "serial": "A", "tenant": {"id": "t1", "last_updated": "1"},
           "last_updated": "1", "custom_fields": pre_cf}
    post = {**pre, "tenant": {"id": "t1", "last_updated": "2"}, "last_updated": "2", "custom_fields": post_cf,
            **extra_post}
    return {"snapshots": {"prechange": pre, "postchange": post}}


def test_glue_write_back_is_ignored():
    assert server._only_glue_fields_changed(_event({"msp_zabbix_hostid": None}, {"msp_zabbix_hostid": "10"}))


def test_human_change_is_not_ignored():
    assert not server._only_glue_fields_changed(_event({}, {}, serial="B"))
    assert not server._only_glue_fields_changed(_event({"msp_monitoring": True}, {"msp_monitoring": False}))


def test_create_event_without_snapshots_is_not_ignored():
    assert not server._only_glue_fields_changed({"snapshots": {"prechange": None, "postchange": {"id": "x"}}})


# ------------------------------------------------------------------------------- dry run
class _Recorder:
    """Stands in for the Nautobot, GLPI and Zabbix clients: lookups find nothing, writes are recorded."""

    def __init__(self):
        self.writes = []

    def find(self, *args, **kwargs):
        return []

    def first(self, *args, **kwargs):
        return None

    def get(self, *args, **kwargs):
        raise LookupError("not found")

    def __getattr__(self, name):  # add, update, ensure, patch, ensure_hostgroup, host_create, ...
        def write(*args, **kwargs):
            self.writes.append(name)
            return "1"
        return write


def test_dry_run_device_sync_writes_nothing(monkeypatch):
    from msp_glue import devices
    from msp_glue.settings import settings

    monkeypatch.setattr(settings, "dry_run", True)
    tools, nb = _Recorder(), _Recorder()
    ctx = devices._Context(tenant=tenant(msp_glpi_entity_id=2), z=tools, g=tools, templates_cache={}, dropdown_cache={})
    device = {"id": "d1", "name": "rtr01", "status": {"name": "Active"}, "role": {"name": "router"},
              "platform": {"name": "Cisco IOS-XE", "network_driver": "cisco_xe"}, "location": {"name": "HQ"},
              "device_type": {"model": "C8200", "manufacturer": {"name": "Cisco"}},
              "primary_ip4": {"host": "10.0.0.1"}, "custom_fields": {}}
    report = devices.SyncReport()
    devices._sync_device(ctx, nb, device, None, report)
    assert tools.writes == [] and nb.writes == []
    assert report.created == ["acme/rtr01"]


def test_dry_run_is_refused_outside_device_sync():
    from msp_glue import cli

    with pytest.raises(SystemExit):
        cli.main(["--dry-run", "tenant", "sync", "acme"])
