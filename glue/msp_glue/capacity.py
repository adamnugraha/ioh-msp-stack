"""Link capacity early warning (kit 1.6.0).

Once a day msp-glue looks at every link of every customer: each interface that ends a circuit in Nautobot (and
interfaces whose description says WAN, uplink, internet or IOH). It reads the link's hourly traffic from Zabbix,
takes each day's busiest hour, and draws a straight trend through those days (Theil-Sen: the median of the
slopes between every pair of days, so one unusual day does not tilt it). Capacity is the circuit's commit rate,
else the interface speed in Nautobot, else the speed Zabbix reads from the device.

A link is "over" when its last 7 busy hours average more than warn_percent of capacity, and "at risk" when the
trend reaches warn_percent within horizon_days. For each link over or at risk msp-glue opens one GLPI ticket in
the customer's entity, for the NOC group, and does not open another while that one is open.

The result goes to <CONSOLE_DATA_DIR>/capacity.json, which the MSP Console reads (customer page, operations page,
monthly report). Settings: mappings.yaml, section capacity.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import clients
from .clients.nautobot import DEVICES_QUERY
from .common import Tenant
from .settings import settings

log = logging.getLogger("msp_glue.capacity")

INTERFACES_QUERY = """
query ($ids: [String]) {
  interfaces(device_id: $ids) {
    id name description speed
    device { id name location { name } }
    cable_peer_circuit_termination { circuit { cid commit_rate provider { name } } }
  }
}
"""
DEFAULT_MATCH = r"(?i)\b(wan|uplink|internet|ioh)\b"
_PREFIXES = [("hundredgigabitethernet", "hu"), ("hundredgige", "hu"), ("fortygigabitethernet", "fo"),
             ("twentyfivegigabitethernet", "twe"), ("twentyfivegige", "twe"), ("tengigabitethernet", "te"),
             ("gigabitethernet", "gi"), ("fastethernet", "fa"), ("port-channel", "po"), ("ethernet", "et"),
             ("eth", "et")]


def _config() -> dict:
    return settings.mappings.get("capacity") or {}


def results_path() -> Path:
    return settings.console_data_dir / "capacity.json"


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TZ") or "Asia/Jakarta")
    except Exception:  # noqa: BLE001 - an unknown zone name: use WIB
        return ZoneInfo("Asia/Jakarta")


# ================================================================================ maths
def theil_sen(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Slope and intercept of the median line through the points (needs two points or more)."""
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(points) for x2, y2 in points[i + 1:] if x2 != x1]
    slope = statistics.median(slopes) if slopes else 0.0
    intercept = statistics.median(y - slope * x for x, y in points)
    return slope, intercept


def daily_busy_hours(rows: list[dict], tz: ZoneInfo) -> dict[str, float]:
    """Each local day's busiest hour (largest hourly average) from Zabbix trend rows."""
    out: dict[str, float] = {}
    for row in rows:
        day = datetime.fromtimestamp(int(row["clock"]), tz).strftime("%Y-%m-%d")
        value = float(row.get("value_avg") or 0)
        if value > out.get(day, -1.0):
            out[day] = value
    return out


def forecast(busy: dict[str, float], capacity_bps: float, cfg: dict | None = None, today: str | None = None) -> dict:
    """Status and forecast of one link direction from its daily busy hours."""
    cfg = {**_config(), **(cfg or {})}
    warn = float(cfg.get("warn_percent", 80)) / 100 * capacity_bps
    horizon, min_days = int(cfg.get("horizon_days", 45)), int(cfg.get("min_days", 14))
    days = sorted(busy)
    # The day still running is incomplete: its busiest hour may not have happened yet.
    if today and days and days[-1] == today:
        days = days[:-1]
    if not days:
        return {"status": "no_data", "days": 0}
    first = datetime.strptime(days[0], "%Y-%m-%d")
    points = [((datetime.strptime(d, "%Y-%m-%d") - first).days, busy[d]) for d in days]
    recent = statistics.mean(v for _, v in points[-7:])
    out = {"days": len(points), "busy_bps": round(recent), "peak_bps": round(max(v for _, v in points)),
           "utilisation": round(100 * recent / capacity_bps, 1) if capacity_bps else None,
           "slope_bps_per_day": None, "days_to_warn": None}
    if not capacity_bps:
        return {**out, "status": "no_capacity"}
    if recent >= warn:
        return {**out, "status": "over", "days_to_warn": 0}
    if len(points) < min_days:
        return {**out, "status": "too_little_data"}
    slope, intercept = theil_sen(points)
    out["slope_bps_per_day"] = round(slope)
    level_now = max(intercept + slope * points[-1][0], recent)
    if slope <= 0:
        return {**out, "status": "ok"}
    days_left = (warn - level_now) / slope
    out["days_to_warn"] = round(max(days_left, 0), 1) if days_left < 730 else None
    return {**out, "status": "at_risk" if days_left <= horizon else "ok"}


# ============================================================================= matching
def norm_interface(name: str) -> str:
    """GigabitEthernet0/0/1, Gi0/0/1 and gi 0/0/1 all become gi0/0/1; Ethernet1/1 and Eth1/1 become et1/1."""
    text = re.sub(r"\s+", "", (name or "").lower())
    for long, short in _PREFIXES:
        if text.startswith(long):
            return short + text[len(long):]
    return text


def _interface_of(item: dict) -> str:
    for tag in item.get("tags") or []:
        if tag["tag"] == "interface":
            return tag["value"]
    match = re.match(r"^Interface ([^():]+?)(?:\(.*\))?:", item.get("name") or "")
    return match.group(1).strip() if match else ""


@dataclass
class Link:
    customer: str
    customer_name: str
    device: str
    device_id: str
    site: str
    interface: str
    description: str = ""
    circuit: str = ""
    provider: str = ""
    capacity_bps: float = 0
    capacity_from: str = ""
    direction: str = ""
    status: str = "no_data"
    busy_bps: float | None = None
    peak_bps: float | None = None
    utilisation: float | None = None
    slope_bps_per_day: float | None = None
    days_to_warn: float | None = None
    days: int = 0
    history: list = field(default_factory=list)
    ticket_id: int | None = None

    @property
    def key(self) -> str:
        return f"{self.customer}|{self.device}|{self.interface}"


# ================================================================================ links
def tenant_links(tenant: Tenant, nb=None, z=None) -> tuple[list[Link], list[str]]:
    """Forecast every link of one customer."""
    nb, z = nb or clients.nautobot(), z or clients.zabbix()
    cfg = _config()
    tz = _tz()
    errors: list[str] = []
    devices = {d["id"]: d for d in nb.graphql(DEVICES_QUERY, {"tenant": [tenant.name]})["devices"]}
    if not devices:
        return [], errors
    matcher = re.compile(cfg.get("description_match") or DEFAULT_MATCH)
    ifaces = [i for i in nb.graphql(INTERFACES_QUERY, {"ids": list(devices)})["interfaces"]
              if i.get("cable_peer_circuit_termination") or matcher.search(i.get("description") or "")]
    if not ifaces:
        return [], errors
    hosts = {}
    for h in z.call("host.get", {"output": ["hostid"], "selectTags": ["tag", "value"],
                                 "tags": [{"tag": "managed_by", "value": "msp-glue", "operator": 1},
                                          {"tag": "tenant", "value": tenant.code, "operator": 1}]}):
        tags = {t["tag"]: t["value"] for t in h.get("tags", [])}
        if tags.get("nautobot_id"):
            hosts[tags["nautobot_id"]] = h["hostid"]
    wanted_hosts = sorted({hosts[i["device"]["id"]] for i in ifaces if i["device"]["id"] in hosts})
    items = z.call("item.get", {"hostids": wanted_hosts, "output": ["itemid", "hostid", "key_", "name", "units"],
                                "selectTags": ["tag", "value"], "search": {"key_": "net.if."}, "filter": {"status": 0}}
                   ) if wanted_hosts else []
    by_host: dict[str, dict[str, dict]] = {}
    for item in items:
        key = item["key_"]
        kind = ("in" if key.startswith("net.if.in[") else "out" if key.startswith("net.if.out[") else
                "speed" if key.startswith("net.if.speed[") else None)
        if not kind or "error" in key or "discard" in key:
            continue
        name = norm_interface(_interface_of(item))
        if name:
            by_host.setdefault(item["hostid"], {}).setdefault(name, {})[kind] = item
    since = int(time.time()) - int(cfg.get("history_days", 28)) * 86400 - 86400
    traffic_ids = [i["itemid"] for per in by_host.values() for e in per.values() for k, i in e.items() if k != "speed"]
    trends: dict[str, list[dict]] = {}
    for start in range(0, len(traffic_ids), 50):
        for row in z.call("trend.get", {"itemids": traffic_ids[start:start + 50], "time_from": since,
                                        "output": ["itemid", "clock", "value_avg"]}):
            trends.setdefault(row["itemid"], []).append(row)
    speeds = {}
    speed_ids = [e["speed"]["itemid"] for per in by_host.values() for e in per.values() if "speed" in e]
    if speed_ids:
        speeds = {i["itemid"]: float(i.get("lastvalue") or 0) for i in z.call(
            "item.get", {"itemids": speed_ids, "output": ["itemid", "lastvalue"]})}
    today = datetime.now(tz).strftime("%Y-%m-%d")
    links = []
    for i in ifaces:
        device = devices.get(i["device"]["id"]) or {}
        circuit = ((i.get("cable_peer_circuit_termination") or {}).get("circuit")) or {}
        link = Link(customer=tenant.code, customer_name=tenant.name, device=i["device"]["name"],
                    device_id=i["device"]["id"], site=(device.get("location") or {}).get("name", ""),
                    interface=i["name"], description=i.get("description") or "", circuit=circuit.get("cid") or "",
                    provider=(circuit.get("provider") or {}).get("name") or "")
        entry = by_host.get(hosts.get(link.device_id, ""), {}).get(norm_interface(i["name"]), {})
        if circuit.get("commit_rate"):
            link.capacity_bps, link.capacity_from = float(circuit["commit_rate"]) * 1000, "circuit"
        elif i.get("speed"):
            link.capacity_bps, link.capacity_from = float(i["speed"]) * 1000, "interface"
        elif "speed" in entry and speeds.get(entry["speed"]["itemid"]):
            link.capacity_bps, link.capacity_from = speeds[entry["speed"]["itemid"]], "device"
        if not entry.get("in") and not entry.get("out"):
            link.status = "no_data" if link.device_id in hosts else "not_monitored"
            links.append(link)
            continue
        best = None
        for direction in ("in", "out"):
            item = entry.get(direction)
            if not item:
                continue
            busy = daily_busy_hours(trends.get(item["itemid"], []), tz)
            result = forecast(busy, link.capacity_bps, cfg, today)
            result["direction"] = direction
            result["history"] = [[d, round(v)] for d, v in sorted(busy.items()) if d != today]
            if best is None or _worse(result, best):
                best = result
        for name in ("status", "busy_bps", "peak_bps", "utilisation", "slope_bps_per_day", "days_to_warn", "days",
                     "direction", "history"):
            if name in best:
                setattr(link, name, best[name])
        links.append(link)
    return links, errors


_RANK = {"over": 0, "at_risk": 1, "ok": 2, "too_little_data": 3, "no_capacity": 4, "no_data": 5, "not_monitored": 6}


def _worse(a: dict, b: dict) -> bool:
    """Is direction a closer to trouble than direction b?"""
    ra, rb = _RANK.get(a["status"], 9), _RANK.get(b["status"], 9)
    if ra != rb:
        return ra < rb
    da, db = a.get("days_to_warn"), b.get("days_to_warn")
    if da is not None and db is not None and da != db:
        return da < db
    return (a.get("utilisation") or 0) > (b.get("utilisation") or 0)


# ================================================================================ run
def due(now: datetime | None = None) -> bool:
    """Is today's run still to do? (after the configured hour, once a day, also across restarts)"""
    now = now or datetime.now(_tz())
    if now.hour < int(_config().get("hour", 3)):
        return False
    generated = load().get("generated")
    if not generated:
        return True
    try:
        last = datetime.fromisoformat(generated).astimezone(now.tzinfo or _tz())
    except ValueError:
        return True
    return last.date() != now.date()


def load() -> dict:
    try:
        return json.loads(results_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"generated": None, "links": [], "errors": [], "tickets": {}}


def _save(result: dict) -> None:
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def run(code: str = "", tickets: bool | None = None) -> dict:
    """Forecast every customer's links (or one customer's), open tickets, and save the result for the console."""
    from .tenants import managed_tenants
    cfg = _config()
    tickets = bool(cfg.get("tickets", True)) if tickets is None else tickets
    previous = load()
    nb, z = clients.nautobot(), clients.zabbix()
    customers = [t for t in managed_tenants(nb) if t.valid and (not code or t.code == code)]
    links: list[Link] = []
    errors: list[str] = []
    for tenant in customers:
        try:
            found, problems = tenant_links(tenant, nb, z)
            links.extend(found)
            errors.extend(f"{tenant.code}: {p}" for p in problems)
        except Exception as exc:  # noqa: BLE001 - one customer must not stop the others
            log.exception("Capacity forecast for %s failed", tenant.code)
            errors.append(f"{tenant.code}: {type(exc).__name__}: {exc}"[:300])
    known = dict(previous.get("tickets") or {})
    opened = 0
    if tickets and not settings.dry_run:
        opened = _tickets(links, customers, known, errors)
    for link in links:
        link.ticket_id = known.get(link.key)
    kept = [asdict(link) for link in links]
    if code:   # one customer only: keep the others' last results
        kept = [x for x in previous.get("links", []) if x.get("customer") != code] + kept
    result = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "settings": {k: cfg.get(k) for k in ("warn_percent", "horizon_days", "history_days", "min_days")},
              "links": kept, "errors": errors, "tickets": known}
    if not settings.dry_run:
        _save(result)
    summary = {"links": len(links), "over": sum(1 for x in links if x.status == "over"),
               "at risk": sum(1 for x in links if x.status == "at_risk"), "tickets opened": opened,
               "errors": len(errors)}
    log.info("Capacity forecast: %s", summary)
    return summary


def _tickets(links: list[Link], customers: list[Tenant], known: dict, errors: list[str]) -> int:
    """One GLPI ticket per link over or at risk, while none is open for that link."""
    from .bootstrap import _ensure_categories
    trouble = [x for x in links if x.status in ("over", "at_risk")]
    if not trouble:
        return 0
    entities = {t.code: t.glpi_entity_id for t in customers}
    opened = 0
    with clients.glpi() as g:
        category_name = _config().get("ticket_category") or "Managed infrastructure > Capacity warning"
        category = _ensure_categories(g, [category_name]).get(" > ".join(p.strip() for p in category_name.split(">")))
        noc = g.find("Group", name=settings.mappings["glpi"]["noc_group"], entities_id=0)
        for link in trouble:
            entity = entities.get(link.customer)
            if not entity:
                continue
            ticket_id = known.get(link.key)
            if ticket_id and _is_open(g, ticket_id):
                continue
            try:
                fields = {"name": ticket_title(link)[:250], "content": ticket_text(link), "entities_id": int(entity),
                          "type": 2, "urgency": 3 if link.status == "over" else 2, "impact": 2}
                if category:
                    fields["itilcategories_id"] = category
                if noc:
                    fields["_groups_id_assign"] = int(noc[0]["id"])
                known[link.key] = g.add("Ticket", fields)
                opened += 1
                log.info("GLPI: capacity ticket %s for %s", known[link.key], link.key)
            except Exception as exc:  # noqa: BLE001 - try the next link
                errors.append(f"{link.customer}: ticket for {link.device} {link.interface}: {exc}"[:300])
    return opened


def _is_open(g, ticket_id) -> bool:
    try:
        return int(g.get("Ticket", int(ticket_id)).get("status") or 0) not in (5, 6)   # 5 solved, 6 closed
    except Exception:  # noqa: BLE001 - deleted: open a new one
        return False


def rate(bps: float | None) -> str:
    if bps is None:
        return "unknown"
    for unit, size in (("Gbps", 1e9), ("Mbps", 1e6), ("kbps", 1e3)):
        if bps >= size:
            return f"{bps / size:.1f} {unit}".replace(".0 ", " ")
    return f"{bps:.0f} bps"


def ticket_title(link: Link) -> str:
    where = f"{link.device} {link.interface}" + (f" (circuit {link.circuit})" if link.circuit else "")
    if link.status == "over":
        return f"Capacity: {where} is above {_config().get('warn_percent', 80)}% at its busy hour"
    return f"Capacity: {where} reaches {_config().get('warn_percent', 80)}% in about {link.days_to_warn:.0f} days"


def ticket_text(link: Link) -> str:
    warn = _config().get("warn_percent", 80)
    direction = {"in": "received by the device", "out": "sent by the device"}.get(link.direction, "")
    lines = [
        f"Link {html.escape(link.interface)} on {html.escape(link.device)} at {html.escape(link.site or 'the site')}"
        + (f", circuit {html.escape(link.circuit)}" + (f" from {html.escape(link.provider)}" if link.provider else "")
           if link.circuit else "") + ".",
        f"Capacity {rate(link.capacity_bps)} (from the {html.escape(link.capacity_from or 'unknown')} speed). Busiest hour over the "
        f"last 7 days: {rate(link.busy_bps)} {direction}, {link.utilisation}% of capacity. Highest busy hour in "
        f"{link.days} days: {rate(link.peak_bps)}.",
    ]
    if link.status == "over":
        lines.append(f"The link is already above the {warn}% warning level at its busy hour.")
    else:
        lines.append(f"Busy-hour traffic grows by about {rate(link.slope_bps_per_day)} a day; at this pace it reaches "
                     f"{warn}% in about {link.days_to_warn:.0f} days.")
    lines.append("Suggested next step: confirm the growth with the customer and offer a bandwidth upgrade or a traffic "
                 "review before users notice slowness.")
    lines.append("Opened by msp-glue (capacity early warning). Another ticket opens for this link only after this "
                 "one is solved and the link is still at risk.")
    return "".join(f"<p>{line}</p>" for line in lines)
