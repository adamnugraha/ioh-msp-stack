"""Link capacity early warning: busy hours, the trend, matching interfaces and the tickets (kit 1.6.0)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("GLUE_MAPPINGS", str(Path(__file__).resolve().parent.parent / "mappings.yaml"))

from msp_glue import capacity  # noqa: E402
from msp_glue.common import Tenant  # noqa: E402
from msp_glue.settings import settings  # noqa: E402

WIB = ZoneInfo("Asia/Jakarta")
MBPS = 1_000_000


def days(values: list[float], start: str = "2026-10-01") -> dict[str, float]:
    first = datetime.strptime(start, "%Y-%m-%d")
    return {(first + timedelta(days=i)).strftime("%Y-%m-%d"): v for i, v in enumerate(values)}


# ---------------------------------------------------------------------------------- maths
def test_theil_sen_ignores_one_wild_day():
    points = [(x, 10 + 2 * x) for x in range(20)]
    points[10] = (10, 500)            # one day of a backup running over the link
    slope, intercept = capacity.theil_sen(points)
    assert slope == pytest.approx(2) and intercept == pytest.approx(10)


def test_busy_hour_is_the_largest_hourly_average_of_each_local_day():
    base = int(datetime(2026, 10, 1, 0, 0, tzinfo=WIB).timestamp())
    rows = [{"clock": base + h * 3600, "value_avg": str(h)} for h in range(48)]   # two days, rising every hour
    assert capacity.daily_busy_hours(rows, WIB) == {"2026-10-01": 23.0, "2026-10-02": 47.0}


def test_forecast_statuses():
    cfg = {"warn_percent": 80, "horizon_days": 45, "min_days": 14}
    growing = days([50 * MBPS + d * MBPS for d in range(28)])               # 50 to 77 Mbps on a 100 Mbps circuit
    result = capacity.forecast(growing, 100 * MBPS, cfg)
    assert result["status"] == "at_risk" and result["slope_bps_per_day"] == MBPS
    assert 0 < result["days_to_warn"] < 10
    flat = days([30 * MBPS] * 28)
    assert capacity.forecast(flat, 100 * MBPS, cfg)["status"] == "ok"
    full = days([85 * MBPS] * 10)
    over = capacity.forecast(full, 100 * MBPS, cfg)
    assert over["status"] == "over" and over["days_to_warn"] == 0          # over needs no minimum history
    assert capacity.forecast(days([50 * MBPS] * 5), 100 * MBPS, cfg)["status"] == "too_little_data"
    assert capacity.forecast({}, 100 * MBPS, cfg)["status"] == "no_data"
    assert capacity.forecast(flat, 0, cfg)["status"] == "no_capacity"
    slow = days([10 * MBPS + d * 10_000 for d in range(28)])                 # far away: not at risk yet
    far = capacity.forecast(slow, 100 * MBPS, cfg)
    assert far["status"] == "ok" and far["days_to_warn"] is None


def test_the_day_still_running_is_left_out():
    values = days([40 * MBPS] * 20 + [1 * MBPS])        # today has hardly started
    today = max(values)
    assert capacity.forecast(values, 100 * MBPS, {}, today)["busy_bps"] == 40 * MBPS


@pytest.mark.parametrize("a, b", [("GigabitEthernet0/0/1", "Gi0/0/1"), ("TenGigabitEthernet1/1", "Te1/1"),
                                  ("Ethernet1/49", "Eth1/49"), ("Port-channel10", "Po10"), ("ge-0/0/0", "ge-0/0/0")])
def test_interface_names_match_in_long_and_short_form(a, b):
    assert capacity.norm_interface(a) == capacity.norm_interface(b)


# -------------------------------------------------------------------------------- links
class FakeNautobot:
    def graphql(self, query, variables):
        if "devices(" in query:
            return {"devices": [{"id": "d1", "name": "rtr01", "location": {"name": "Jakarta HQ"}}]}
        return {"interfaces": [
            {"id": "i1", "name": "GigabitEthernet0/0/0", "description": "", "speed": 1_000_000,
             "device": {"id": "d1", "name": "rtr01"},
             "cable_peer_circuit_termination": {"circuit": {"cid": "IOH-123", "commit_rate": 100_000,
                                                            "provider": {"name": "IOH"}}}},
            {"id": "i2", "name": "GigabitEthernet0/0/1", "description": "LAN", "speed": None,
             "device": {"id": "d1", "name": "rtr01"}, "cable_peer_circuit_termination": None},
            {"id": "i3", "name": "GigabitEthernet0/0/2", "description": "Backup WAN", "speed": None,
             "device": {"id": "d1", "name": "rtr01"}, "cable_peer_circuit_termination": None},
        ]}


class FakeZabbix:
    def __init__(self):
        now = datetime.now(WIB).replace(hour=12, minute=0, second=0, microsecond=0)
        self.rows = []
        for back in range(1, 29):
            day = now - timedelta(days=back)
            for hour, scale in ((3, 0.2), (14, 1.0)):
                clock = int(day.replace(hour=hour).timestamp())
                busy = (50 + (28 - back)) * MBPS        # 51 Mbps four weeks ago, 77 Mbps yesterday
                self.rows.append({"itemid": "101", "clock": clock, "value_avg": str(busy * scale)})
                self.rows.append({"itemid": "102", "clock": clock, "value_avg": str(5 * MBPS * scale)})

    def call(self, method, params):
        if method == "host.get":
            return [{"hostid": "500", "tags": [{"tag": "nautobot_id", "value": "d1"}]}]
        if method == "item.get" and "search" in params:
            return [
                {"itemid": "101", "hostid": "500", "key_": "net.if.in[ifHCInOctets.1]", "name": "Interface Gi0/0/0(): Bits received",
                 "tags": [{"tag": "interface", "value": "Gi0/0/0"}]},
                {"itemid": "102", "hostid": "500", "key_": "net.if.out[ifHCOutOctets.1]", "name": "Interface Gi0/0/0(): Bits sent",
                 "tags": [{"tag": "interface", "value": "Gi0/0/0"}]},
                {"itemid": "103", "hostid": "500", "key_": "net.if.in.errors[ifInErrors.1]", "name": "x", "tags": []},
            ]
        if method == "trend.get":
            return [r for r in self.rows if r["itemid"] in params["itemids"]]
        return []


def acme() -> Tenant:
    return Tenant.from_nautobot({"id": "t1", "name": "PT ACME", "custom_fields": {
        "msp_code": "acme", "msp_glpi_entity_id": 7}})


def test_links_are_circuits_and_wan_described_interfaces():
    links, errors = capacity.tenant_links(acme(), FakeNautobot(), FakeZabbix())
    assert not errors
    assert [x.interface for x in links] == ["GigabitEthernet0/0/0", "GigabitEthernet0/0/2"]   # the LAN port is left out
    wan = links[0]
    assert (wan.circuit, wan.capacity_bps, wan.capacity_from, wan.site) == ("IOH-123", 100 * MBPS, "circuit", "Jakarta HQ")
    assert wan.direction == "in" and wan.status == "at_risk"
    assert 70 < wan.utilisation < 78 and wan.days_to_warn is not None and wan.days_to_warn < 10
    assert len(wan.history) == 28
    backup = links[1]
    assert backup.status == "no_data" and backup.capacity_bps == 0      # no traffic items for it in Zabbix


class FakeGLPI:
    def __init__(self, status=2):
        self.added, self.status = [], status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def find(self, itemtype, **criteria):
        return [{"id": 9}] if itemtype == "Group" else []

    def ensure(self, itemtype, match, fields=None):
        return 40 + len(match["name"])

    def get(self, itemtype, item_id):
        return {"id": item_id, "status": self.status}

    def add(self, itemtype, fields):
        self.added.append(fields)
        return 300 + len(self.added)


def test_run_saves_results_and_opens_one_ticket_per_link(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "console_data_dir", tmp_path)
    glpi = FakeGLPI()
    monkeypatch.setattr("msp_glue.tenants.managed_tenants", lambda nb=None: [acme()])
    monkeypatch.setattr(capacity.clients, "nautobot", lambda: FakeNautobot())
    monkeypatch.setattr(capacity.clients, "zabbix", lambda: FakeZabbix())
    monkeypatch.setattr(capacity.clients, "glpi", lambda: glpi)
    summary = capacity.run()
    assert summary["at risk"] == 1 and summary["tickets opened"] == 1
    ticket = glpi.added[0]
    assert ticket["entities_id"] == 7 and ticket["_groups_id_assign"] == 9 and ticket["type"] == 2
    assert "rtr01 GigabitEthernet0/0/0 (circuit IOH-123) reaches 80%" in ticket["name"]
    assert "bandwidth upgrade" in ticket["content"]
    saved = json.loads((tmp_path / "capacity.json").read_text())
    assert saved["tickets"] == {"acme|rtr01|GigabitEthernet0/0/0": 301}
    assert saved["links"][0]["ticket_id"] == 301
    # the ticket is still open: no second one
    capacity.run()
    assert len(glpi.added) == 1
    # solved, and the link is still at risk: a new one
    glpi.status = 5
    capacity.run()
    assert len(glpi.added) == 2


def test_daily_run_is_due_once_after_the_hour(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "console_data_dir", tmp_path)
    morning = datetime(2026, 11, 7, 2, 0, tzinfo=WIB)
    assert not capacity.due(morning)                           # before 03:00
    assert capacity.due(morning.replace(hour=4))
    (tmp_path / "capacity.json").write_text(json.dumps({"generated": "2026-11-06T21:30:00+00:00"}))   # 04:30 WIB
    assert not capacity.due(morning.replace(hour=9))
    assert capacity.due((morning + timedelta(days=1)).replace(hour=4))
