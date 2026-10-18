"""Read-only views over every tool for the MSP Console.

Each loader asks one tool for everything the pages need and keeps the answer for
CONSOLE_CACHE_SECONDS, so ten engineers refreshing the same page cost one round of API calls.
A tool that does not answer never breaks a page: its loader returns empty data and the page
names the tool that failed.
"""

from __future__ import annotations

import html
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .. import clients
from ..clients.glpi import STATUS_SOLVED
from ..common import Tenant, host_ip
from ..http import make_session
from ..settings import settings
from ..tenants import managed_tenants

log = logging.getLogger("msp_glue.console.data")

SEVERITIES = {0: "Not classified", 1: "Information", 2: "Warning", 3: "Average", 4: "High", 5: "Disaster"}
TICKET_STATUS = {1: "New", 2: "Assigned", 3: "Planned", 4: "Waiting", 5: "Solved", 6: "Closed"}
PRIORITY = {1: "Very low", 2: "Low", 3: "Medium", 4: "High", 5: "Very high", 6: "Major"}
# GLPI change statuses (Change::getAllStatusArray)
CHANGE_STATUS = {1: "New", 9: "Evaluation", 10: "Approval", 7: "Accepted", 4: "Pending", 11: "Test",
                 12: "Qualification", 5: "Applied", 8: "Review", 6: "Closed", 14: "Refused", 13: "Cancelled"}
CHANGE_DONE = {"Applied", "Closed", "Refused", "Cancelled", "Review"}
CHANGE_MARK = re.compile(r"\bchange\s*#\s*(\d+)", re.I)


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TZ") or "Asia/Jakarta")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def now() -> datetime:
    return datetime.now(tz())


# ----------------------------------------------------------------------------- cache
class _Cache:
    """Answers kept per key for a few seconds.

    A stale answer (up to three times the time to live) is returned at once while a background
    thread fetches a new one, so pages stay fast. Older answers are fetched before the page renders.
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.errors: dict[str, str] = {}

    def get(self, key: str, loader, fallback, ttl: int | None = None):
        ttl = settings.console_cache_seconds if ttl is None else ttl
        hit = self._data.get(key)
        age = time.time() - hit[0] if hit else None
        if hit and age < ttl:
            return hit[1]
        if hit and age < 3 * ttl:
            self._refresh_later(key, loader)
            return hit[1]
        with self._locks[key]:
            hit = self._data.get(key)   # another request may have loaded it meanwhile
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            return self._load(key, loader, hit, fallback)

    def _load(self, key: str, loader, hit, fallback):
        try:
            value = loader()
            self.errors.pop(key, None)
        except Exception as exc:  # noqa: BLE001 - one tool down must not break the page
            log.warning("Console: loading %s failed: %s", key, exc)
            self.errors[key] = f"{type(exc).__name__}: {exc}"[:300]
            return hit[1] if hit else fallback
        self._data[key] = (time.time(), value)
        return value

    def _refresh_later(self, key: str, loader) -> None:
        lock = self._locks[key]
        if not lock.acquire(blocking=False):
            return   # already being refreshed

        def run() -> None:
            try:
                self._load(key, loader, self._data.get(key), None)
            finally:
                lock.release()
        threading.Thread(target=run, daemon=True, name=f"refresh {key}").start()

    def loaded(self, key: str) -> datetime | None:
        hit = self._data.get(key)
        return datetime.fromtimestamp(hit[0], tz()) if hit else None

    def oldest(self, *keys: str) -> datetime | None:
        """When the oldest of these answers was read (for "data as of")."""
        found = [self.loaded(k) for k in keys]
        found = [f for f in found if f]
        return min(found) if found else None

    def clear(self, *keys: str) -> None:
        for key in keys or list(self._data):
            self._data.pop(key, None)

    def clear_prefix(self, prefix: str) -> None:
        self.clear(*[k for k in list(self._data) if k.startswith(prefix)])


cache = _Cache()
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="console-load")
# The health probes have threads of their own: queued behind page loaders that are themselves waiting
# for the health answer, they would never run.
_probes = ThreadPoolExecutor(max_workers=7, thread_name_prefix="console-probe")
PROBE_SECONDS = 8


def prefetch(*loaders) -> None:
    """Run several loaders at the same time, so a page waits for the slowest tool, not the sum."""
    for future in [_pool.submit(loader) for loader in loaders]:
        try:
            future.result(timeout=90)
        except Exception:  # noqa: BLE001 - loaders record their own errors
            pass


# ------------------------------------------------------------------------- formatting
def ago(when: datetime | None) -> str:
    if not when:
        return "never"
    seconds = int((now() - when).total_seconds())
    if seconds < 0:
        return "in " + ago(now() - timedelta(seconds=-seconds)).replace(" ago", "")
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    return f"{hours // 24} days ago"


def duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    if seconds < 172800:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    return f"{seconds // 86400} days {seconds % 86400 // 3600} h"


GLPI_FORMAT = "%Y-%m-%d %H:%M:%S"


_offset: dict = {"value": None, "at": 0.0}
_offset_lock = threading.Lock()


def glpi_offset() -> int | None:
    """How far GLPI's clock is from UTC, in seconds. GLPI reads and writes date-times as text in its own time
    zone (PHP's, UTC in the official image, unless GLPI's time zone support is on), so the console asks GLPI
    for its current time once an hour instead of assuming one. None until GLPI has answered once."""
    def fresh() -> bool:
        return time.time() - _offset["at"] < (3600 if _offset["value"] is not None else 120)
    if fresh():
        return _offset["value"]
    with _offset_lock:
        if not fresh():
            try:
                with clients.glpi() as g:
                    session = (g.request("GET", "getFullSession") or {}).get("session") or {}
                clock = datetime.strptime(str(session["glpi_currenttime"])[:19], GLPI_FORMAT)
                ahead = (clock - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
                _offset["value"] = int(round(ahead / 900) * 900)    # time zones are whole quarter hours
            except Exception as exc:  # noqa: BLE001 - keep what GLPI said before; ask again soon
                log.warning("Console: could not read GLPI's clock: %s", exc)
            _offset["at"] = time.time()
    return _offset["value"]


def glpi_time(value) -> datetime | None:
    """A GLPI date-time ("2026-10-05 23:52:35", in GLPI's time zone) in the platform's time zone."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value)[:19], GLPI_FORMAT)
    except ValueError:
        return None
    offset = glpi_offset()
    if offset is None:      # GLPI not asked yet or not answering: as the platform's time zone
        return parsed.replace(tzinfo=tz())
    return (parsed - timedelta(seconds=offset)).replace(tzinfo=timezone.utc).astimezone(tz())


def to_glpi(value: datetime) -> str:
    """A date-time as GLPI expects it in its search criteria and its fields."""
    offset = glpi_offset()
    if offset is None:
        return value.astimezone(tz()).strftime(GLPI_FORMAT)
    return (value.astimezone(timezone.utc) + timedelta(seconds=offset)).strftime(GLPI_FORMAT)


_glpi_time = glpi_time   # 1.1 name


def iso_time(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz())
    return parsed.astimezone(tz())


_iso_time = iso_time


def local_time(value) -> datetime | None:
    """msp-glue's job times: "2026-10-17 13:05:41" in the platform's time zone (its TZ)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], GLPI_FORMAT).replace(tzinfo=tz())
    except ValueError:
        return None


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", html.unescape(text or ""), flags=re.I)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "", text)).strip()


# ============================================================================ loaders
def customers() -> list[Tenant]:
    return cache.get("customers", lambda: [t for t in managed_tenants() if t.valid], [])


def customer(code: str) -> Tenant | None:
    return next((t for t in customers() if t.code == code), None)


def customer_names() -> dict[str, str]:
    return {t.code: t.name for t in customers()}


DEVICES_QUERY = """
query ($group: [String]) {
  devices(tenant_group: $group) {
    id name serial
    status { name } role { name } platform { name network_driver } device_type { model }
    location { id name parent { name } }
    tenant { id name _custom_field_data }
    primary_ip4 { host } primary_ip6 { host }
    _custom_field_data
  }
}
"""


def devices() -> list[dict]:
    """Every device of every managed customer, from Nautobot."""
    def load():
        group = settings.mappings["tenancy"]["tenant_group"]
        gc_platforms = set(settings.mappings.get("golden_config", {}).get("platforms") or [])
        rows = []
        for d in clients.nautobot().graphql(DEVICES_QUERY, {"group": [group]})["devices"]:
            tenant_cf = (d.get("tenant") or {}).get("custom_fields") or {}
            cf = d.get("custom_fields") or {}
            status = (d.get("status") or {}).get("name", "")
            platform = (d.get("platform") or {}).get("name", "")
            ip = host_ip(d.get("primary_ip4")) or host_ip(d.get("primary_ip6"))
            location = d.get("location") or {}
            rows.append({
                "id": d["id"], "name": d["name"], "serial": d.get("serial") or "",
                "status": status, "role": (d.get("role") or {}).get("name", ""), "platform": platform,
                "driver": (d.get("platform") or {}).get("network_driver", ""),
                "model": (d.get("device_type") or {}).get("model", ""),
                "site": location.get("name", ""), "site_id": location.get("id", ""),
                "region": (location.get("parent") or {}).get("name", ""),
                "customer": (tenant_cf.get("msp_code") or "").lower(), "ip": ip,
                # Golden Config looks after active network devices with an address and a supported platform
                "gc_scope": status == "Active" and bool(ip) and platform in gc_platforms,
                "monitoring": cf.get("msp_monitoring") is not False,
                "drift_ticket": cf.get("msp_drift_ticket_id"),
                "asset_id": cf.get("msp_glpi_asset_id"), "itemtype": cf.get("msp_glpi_itemtype") or "",
            })
        return rows
    return cache.get("devices", load, [])


def device_by_id(device_id: str) -> dict | None:
    return next((d for d in devices() if d["id"] == device_id), None)


def hosts() -> dict[str, dict]:
    """Zabbix hosts msp-glue manages, keyed by Nautobot device id."""
    def load():
        found = clients.zabbix().call("host.get", {
            "output": ["hostid", "host", "name", "status", "maintenance_status"],
            "selectInterfaces": ["type", "available", "error", "main"],
            "selectTags": ["tag", "value"],
            "tags": [{"tag": "managed_by", "value": "msp-glue", "operator": 1}],
        })
        out = {}
        for h in found:
            tags = {t["tag"]: t["value"] for t in h.get("tags", [])}
            main = [i for i in h.get("interfaces", []) if str(i.get("main")) == "1"] or h.get("interfaces", [])
            available = str(main[0].get("available", "0")) if main else "0"
            state = "disabled" if str(h["status"]) == "1" else {"1": "up", "2": "down"}.get(available, "unknown")
            out[tags.get("nautobot_id", h["hostid"])] = {
                "hostid": h["hostid"], "name": h["name"], "state": state, "customer": tags.get("tenant", ""),
                "device": tags.get("device", ""), "error": (main[0].get("error") if main else "") or "",
                "maintenance": str(h.get("maintenance_status")) == "1",
            }
        return out
    return cache.get("hosts", load, {})


def hosts_by_hostid() -> dict[str, dict]:
    return {h["hostid"]: {**h, "nautobot_id": key} for key, h in hosts().items()}


def zabbix_users() -> dict[str, str]:
    """Zabbix user id -> the name to show (for acknowledgements)."""
    def load():
        out = {}
        for u in clients.zabbix().call("user.get", {"output": ["userid", "username", "name", "surname"]}):
            full = " ".join(x for x in (u.get("name"), u.get("surname")) if x).strip()
            out[u["userid"]] = full or u.get("username") or "?"
        return out
    return cache.get("zabbix_users", load, {}, ttl=3600)


_CONSOLE_ACK = re.compile(r"in the MSP Console by (.+?) \(([^)]+)\)", re.I)


def problems() -> list[dict]:
    """Open Zabbix problems on managed hosts, worst first, with their GLPI ticket."""
    def load():
        z = clients.zabbix()
        rows = z.call("problem.get", {
            "output": ["eventid", "objectid", "clock", "name", "severity", "acknowledged", "suppressed", "opdata"],
            "selectTags": "extend", "selectAcknowledges": ["userid", "clock", "message", "action"], "recent": False,
            "tags": [{"tag": "managed_by", "value": "msp-glue", "operator": 1}],
            "sortfield": ["eventid"], "sortorder": "DESC",
        })
        # As Zabbix's own Problems page: only problems of triggers it still checks. A host msp-glue disabled (device
        # removed or no longer active) keeps the problems it had open, and Zabbix never closes them (ZBX-27917).
        triggers = sorted({p["objectid"] for p in rows})
        checked = {t["triggerid"] for t in z.call("trigger.get", {"triggerids": triggers, "monitored": True,
                                                                  "output": ["triggerid"]})} if triggers else set()
        rows = [p for p in rows if p["objectid"] in checked]
        users = zabbix_users()
        zbx, glpi = settings.public_url("zabbix"), settings.public_url("glpi")
        out = []
        for p in rows:
            tags = {}
            for t in p.get("tags", []):
                tags.setdefault(t["tag"], t["value"])
            started = datetime.fromtimestamp(int(p["clock"]), tz())
            severity = int(p["severity"])
            ticket = tags.get("__zbx_glpi_ticket_id", "")
            updates = []
            for a in sorted(p.get("acknowledges") or [], key=lambda a: -int(a.get("clock") or 0)):
                message = (a.get("message") or "").strip()
                if not message and not (int(a.get("action") or 0) & 2):
                    continue
                who = users.get(str(a.get("userid")), "Zabbix")
                console = _CONSOLE_ACK.search(message)
                if console:   # the console acknowledges as its service account and names the engineer
                    who = console.group(1)
                updates.append({"at": datetime.fromtimestamp(int(a["clock"]), tz()), "who": who,
                                "message": message or "Acknowledged."})
            out.append({
                "eventid": p["eventid"], "name": p["name"], "severity": severity,
                "severity_name": SEVERITIES.get(severity, "?"), "started": started,
                "age": duration((now() - started).total_seconds()),
                "acknowledged": str(p["acknowledged"]) == "1", "suppressed": str(p.get("suppressed")) == "1",
                "customer": tags.get("tenant", ""), "customer_name": tags.get("tenant_name", ""),
                "device": tags.get("device", ""), "site": tags.get("site", ""),
                "ticket_id": ticket,
                "ticket_link": f"{glpi}/front/ticket.form.php?id={int(ticket)}" if ticket.isdigit() else None,
                "opdata": p.get("opdata") or "", "updates": updates,
                "zabbix_link": f"{zbx}/tr_events.php?triggerid={p['objectid']}&eventid={p['eventid']}",
            })
        out.sort(key=lambda r: (r["acknowledged"], r["suppressed"], -r["severity"], r["started"]))
        return out
    return cache.get("problems", load, [])


# --------------------------------------------------------------------------- GLPI
_TICKET_FIELDS = {2: "id", 1: "name", 12: "status", 3: "priority", 80: "entity", 15: "opened", 18: "due",
                  8: "group", 5: "technician", 82: "late", 14: "type", 159: "own_late", 9: "source", 7: "category",
                  13: "items", 131: "item_types"}


def _search(itemtype: str, criteria: list[tuple[int, str, object]], fields: dict[int, str]) -> list[dict]:
    params: dict = {"range": "0-999"}
    for i, (field, searchtype, value) in enumerate(criteria):
        if i:
            params[f"criteria[{i}][link]"] = "AND"
        params.update({f"criteria[{i}][field]": field, f"criteria[{i}][searchtype]": searchtype,
                       f"criteria[{i}][value]": value})
    for i, field in enumerate(fields):
        params[f"forcedisplay[{i}]"] = field
    with clients.glpi() as g:
        body = g.request("GET", f"search/{itemtype}", params=params) or {}
    return [{name: row.get(str(field)) for field, name in fields.items()} for row in body.get("data") or []]


def _ticket_search(criteria: list[tuple[int, str, object]], fields: dict[int, str] | None = None) -> list[dict]:
    return _search("Ticket", criteria, fields or _TICKET_FIELDS)


def _customer_by_entity() -> dict[str, str]:
    """GLPI entity complete name ("... > IOH Managed Services > PT ACME") -> customer code."""
    return {t.name: t.code for t in customers()}


def _entity_customer(entity: str, names: dict[str, str]) -> str:
    leaf = (entity or "").split(">")[-1].strip()
    return names.get(html.unescape(leaf), names.get(leaf, ""))


def _multi(value) -> list[str]:
    """The values of a GLPI search column that can hold several: a list, or text joined with $$##$$ where each
    value may carry the id of its link after $#$ ("NetworkEquipment$#$13$$##$$Computer$#$14")."""
    if value in (None, ""):
        return []
    parts = value if isinstance(value, list) else str(value).split("$$##$$")
    out = []
    for part in parts:
        text = html.unescape(str(part if part is not None else "").split("$#$")[0]).strip()
        if text and text not in ("0", "__NULL__"):
            out.append(text)
    return out


def _devices_by_asset() -> dict[tuple[str, str], str]:
    """(GLPI item type, asset id) -> device name, for the assets msp-glue keeps for Nautobot devices."""
    return {(d["itemtype"], str(d["asset_id"])): d["name"] for d in devices() if d.get("asset_id") and d.get("itemtype")}


def _linked_devices(row: dict, by_asset: dict) -> list[str]:
    """Devices among a ticket's or change's associated items (search columns 13 and 131, in the same order)."""
    ids, kinds = _multi(row.get("items")), _multi(row.get("item_types"))
    if len(ids) != len(kinds):
        return []
    return [by_asset[(k, i)] for k, i in zip(kinds, ids) if (k, i) in by_asset]


def _ticket_source(row: dict) -> str:
    category = html.unescape(str(row.get("category") or ""))
    drift = settings.mappings["glpi"]["categories"].get("compliance", "")
    if drift and category.replace(" ", "") == drift.replace(" ", ""):
        return "Configuration drift"
    source = html.unescape(str(row.get("source") or "")).strip().lower()
    # GLPI's request sources; "Helpdesk" is GLPI's default for tickets entered in GLPI itself (customer portal
    # or technicians), the others are GLPI's own list or ones added by an administrator.
    return {"monitoring": "Monitoring", "msp console": "MSP Console", "e-mail": "Email", "email": "Email",
            "phone": "Phone", "helpdesk": "GLPI"}.get(source, "Other")


def open_tickets() -> list[dict]:
    """Tickets that are not solved or closed, late ones first."""
    def load():
        names = _customer_by_entity()
        glpi = settings.public_url("glpi")
        by_asset = _devices_by_asset()
        rows = []
        for r in _ticket_search([(12, "equals", "notold")]):
            due = glpi_time(r.get("due"))
            items = _linked_devices(r, by_asset)
            rows.append({
                "id": int(r["id"]), "name": html.unescape(r.get("name") or ""),
                "status": TICKET_STATUS.get(int(r.get("status") or 0), "?"),
                "priority": int(r.get("priority") or 3), "priority_name": PRIORITY.get(int(r.get("priority") or 3), "?"),
                "customer": _entity_customer(r.get("entity"), names), "opened": glpi_time(r.get("opened")),
                "due": due, "late": str(r.get("late")) == "1" or bool(due and due < now()),
                "group": ", ".join(_multi(r.get("group"))), "technician": ", ".join(_multi(r.get("technician"))),
                "type": "Incident" if str(r.get("type")) == "1" else "Request",
                "source": _ticket_source(r), "device": items[0] if items else None,
                "link": f"{glpi}/front/ticket.form.php?id={int(r['id'])}",
            })
        rows.sort(key=lambda t: (not t["late"], t["due"] or now() + timedelta(days=365), -t["priority"]))
        return rows
    return cache.get("open_tickets", load, [])


def month_start(months_back: int = 0) -> datetime:
    start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(months_back):
        start = (start - timedelta(days=1)).replace(day=1)
    return start


def next_month(start: datetime) -> datetime:
    return (start + timedelta(days=32)).replace(day=1)


def ticket_stats(start: datetime, end: datetime | None = None) -> dict[str, dict]:
    """Per customer, tickets opened from start (to end): solved, service level breached (time to own or time
    to resolve exceeded), mean time to resolve."""
    def load():
        names = _customer_by_entity()
        fields = {2: "id", 12: "status", 80: "entity", 15: "opened", 17: "solved", 82: "late", 159: "own_late",
                  14: "type"}
        criteria = [(15, "morethan", to_glpi(start))]
        if end:
            criteria.append((15, "lessthan", to_glpi(end)))
        stats: dict[str, dict] = defaultdict(lambda: {"opened": 0, "solved": 0, "late": 0, "own_late": 0,
                                                      "resolve_late": 0, "resolve_hours": []})
        for r in _ticket_search(criteria, fields):
            code = _entity_customer(r.get("entity"), names)
            if not code:
                continue
            s = stats[code]
            s["opened"] += 1
            own_late, resolve_late = str(r.get("own_late")) == "1", str(r.get("late")) == "1"
            s["own_late"] += own_late
            s["resolve_late"] += resolve_late
            if own_late or resolve_late:
                s["late"] += 1
            opened, solved = glpi_time(r.get("opened")), glpi_time(r.get("solved"))
            if solved and int(r.get("status") or 0) >= STATUS_SOLVED:
                s["solved"] += 1
                if opened:
                    s["resolve_hours"].append((solved - opened).total_seconds() / 3600)
        out = {}
        for code, s in stats.items():
            hours = s.pop("resolve_hours")
            out[code] = {**s, "mttr_hours": round(sum(hours) / len(hours), 1) if hours else None}
        return out
    return cache.get(f"tickets_from:{start:%Y-%m}", load, {})


def month_tickets() -> dict[str, dict]:
    return ticket_stats(month_start())


_CHANGE_FIELDS = {2: "id", 1: "name", 12: "status", 80: "entity", 7: "category", 15: "opened", 11: "impact",
                  4: "requester", 22: "writer", 173: "task_begin", 174: "task_end", 21: "content", 13: "items",
                  131: "item_types"}
_REQUESTED_BY = re.compile(r"(?:Requested|Raised|Opened) in the MSP Console by ([^<(]+?)\s*\(", re.I)


def changes() -> list[dict]:
    """GLPI changes of managed customers: every unfinished one, and those opened in the last 60 days."""
    def load():
        names = _customer_by_entity()
        glpi = settings.public_url("glpi")
        templates = (settings.mappings.get("automation") or {}).get("change_templates") or {}
        since = to_glpi(now() - timedelta(days=60))
        found: dict[int, dict] = {}
        for criteria in ([(12, "equals", "notold")], [(15, "morethan", since)]):
            for r in _search("Change", criteria, _CHANGE_FIELDS):
                found[int(r["id"])] = r
        by_asset = _devices_by_asset()
        maintenance_by_change = {m["change_id"]: m["id"] for m in maintenances() if m.get("change_id")}
        rows = []
        for change_id, r in found.items():
            code = _entity_customer(r.get("entity"), names)
            if not code:
                continue
            category = html.unescape(str(r.get("category") or ""))
            content = html.unescape(str(r.get("content") or ""))
            starts = [t for t in (glpi_time(v) for v in _multi(r.get("task_begin"))) if t]
            ends = [t for t in (glpi_time(v) for v in _multi(r.get("task_end"))) if t]
            impact = int(r.get("impact") or 3)
            requester = _REQUESTED_BY.search(content)
            variables = _automation_devices(content)
            rows.append({
                "id": change_id, "name": html.unescape(r.get("name") or ""),
                "status": CHANGE_STATUS.get(int(r.get("status") or 0), "New"), "customer": code,
                "category": category, "template": templates.get(category),
                "planned_start": min(starts) if starts else None, "planned_end": max(ends) if ends else None,
                "risk": "Low" if impact <= 2 else "Medium" if impact == 3 else "High",
                "devices": sorted(set(_linked_devices(r, by_asset) + variables)),
                "requester": (requester.group(1).strip() if requester else
                              ", ".join(_multi(r.get("requester"))) or ", ".join(_multi(r.get("writer")))),
                "maintenance_id": maintenance_by_change.get(change_id),
                "opened": glpi_time(r.get("opened")),
                "link": f"{glpi}/front/change.form.php?id={change_id}",
            })
        rows.sort(key=lambda c: (c["status"] in CHANGE_DONE, c["planned_start"] or now() + timedelta(days=3650)))
        return rows
    return cache.get("changes", load, [])


def _automation_devices(content: str) -> list[str]:
    from ..changes import parse_variables
    device = parse_variables(content).get("device", "")
    return [d.strip() for d in device.split(",") if d.strip()]


# ------------------------------------------------------------------------- Zabbix
def maintenances() -> list[dict]:
    """Zabbix maintenance periods that cover managed hosts or customer host groups."""
    def load():
        rows = clients.zabbix().call("maintenance.get", {
            "output": "extend", "selectHostGroups": ["groupid", "name"], "selectHosts": ["hostid", "name"],
            "selectTimeperiods": "extend",
        })
        by_host = hosts_by_hostid()
        root = settings.mappings["zabbix"]["customer_group_root"] + "/"
        by_group = {t.name: t.code for t in customers()}
        zbx = settings.public_url("zabbix")
        out = []
        for m in rows:
            mine = [by_host[h["hostid"]] for h in m.get("hosts") or [] if h["hostid"] in by_host]
            codes = {h["customer"] for h in mine if h["customer"]}
            for g in m.get("hostgroups") or []:
                if g["name"].startswith(root):
                    code = by_group.get(g["name"][len(root):].split("/")[0])
                    if code:
                        codes.add(code)
            if len(codes) != 1:
                continue    # not a customer's, or several customers' (shown in Zabbix only)
            start, end = _maintenance_window(m)
            description = m.get("description") or ""
            change = CHANGE_MARK.search(description) or CHANGE_MARK.search(m.get("name") or "")
            by = re.search(r"in the MSP Console by ([^(\n]+?)\s*(?:\(|$)", description)
            moment = now()
            out.append({
                "id": m["maintenanceid"], "name": m.get("name") or "", "customer": codes.pop(),
                "devices": None if m.get("hostgroups") else sorted(h["device"] or h["name"] for h in mine),
                "start": start, "end": end, "collect_data": str(m.get("maintenance_type")) == "0",
                "change_id": int(change.group(1)) if change else None,
                "created_by": by.group(1).strip() if by else "Zabbix",
                "state": "upcoming" if moment < start else "ended" if moment > end else "active",
                "link": f"{zbx}/zabbix.php?action=maintenance.list&filter_set=1&filter_name={quote(m.get('name') or '')}",
            })
        out.sort(key=lambda m: m["start"])
        return out
    return cache.get("maintenances", load, [])


def _maintenance_window(m: dict) -> tuple[datetime, datetime]:
    """The maintenance's first one-time period, or its overall validity."""
    since = datetime.fromtimestamp(int(m.get("active_since") or 0), tz())
    till = datetime.fromtimestamp(int(m.get("active_till") or 0), tz())
    for p in m.get("timeperiods") or []:
        if str(p.get("timeperiod_type")) == "0" and p.get("start_date"):
            start = datetime.fromtimestamp(int(p["start_date"]), tz())
            return start, start + timedelta(seconds=int(p.get("period") or 0))
    return since, till


def availability() -> dict[str, dict]:
    """Availability per customer from the Zabbix SLA msp-glue creates ("Customer <code> availability", monthly
    period): this month, last month, and the last six months for the trend."""
    def load():
        z = clients.zabbix()
        out = {}
        for sla in z.call("sla.get", {"output": ["slaid", "name", "slo"]}):
            name = sla["name"]
            if not (name.startswith("Customer ") and name.endswith(" availability")):
                continue
            code = name[len("Customer "):-len(" availability")]
            report = z.call("sla.getsli", {"slaid": sla["slaid"], "periods": 6})   # oldest period first
            periods = []
            for index, period in enumerate(report.get("periods") or []):
                rows = (report.get("sli") or [])[index] if index < len(report.get("sli") or []) else []
                cell = rows[0] if rows else None
                valid = cell is not None and float(cell.get("sli", -1)) >= 0   # -1: before the SLA started
                start = datetime.fromtimestamp(int(period["period_from"]), tz())
                periods.append({
                    "month": start.strftime("%Y-%m"), "start": start,
                    "end": datetime.fromtimestamp(int(period["period_to"]), tz()),
                    "sli": round(float(cell["sli"]), 3) if valid else None,
                    "downtime": int(cell["downtime"]) if valid else None,
                    "budget": int(cell["error_budget"]) if valid else None,
                })
            current = periods[-1] if periods else {}
            previous = periods[-2] if len(periods) > 1 else {}
            out[code] = {"slo": float(sla["slo"]), "slaid": sla["slaid"], "sli": current.get("sli"),
                         "downtime": current.get("downtime"), "budget": current.get("budget"),
                         "previous": previous.get("sli"), "periods": periods}
        return out
    return cache.get("availability", load, {})


def downtime_by_day(code: str) -> list[dict]:
    """Minutes of downtime per day this month, exactly as the customer's availability agreement counts them:
    the time the customer's service in Zabbix ("Customer <code>", the one its SLA reports) was in a problem
    state, read from the service's own status history."""
    def load():
        start, moment = month_start(), now()
        days, day = [], start
        while day <= moment:
            following = (day + timedelta(days=1, hours=2)).replace(hour=0)
            days.append({"day": day.strftime("%Y-%m-%d"), "from": int(day.timestamp()),
                         "to": int(following.timestamp())})
            day = following
        found = clients.zabbix().call("service.get", {
            "output": ["serviceid"], "filter": {"name": [f"Customer {code}"]},
            "selectStatusTimeline": [{"period_from": d["from"], "period_to": d["to"]} for d in days]})
        timeline = (found[0].get("status_timeline") or []) if found else []
        end_of_data = int(moment.timestamp())
        out = []
        for index, d in enumerate(days):
            entry = timeline[index] if index < len(timeline) else {"start_value": "-1", "alarms": []}
            seconds, state, since = 0, int(entry.get("start_value", -1)), d["from"]
            for alarm in sorted(entry.get("alarms") or [], key=lambda a: int(a["clock"])):
                clock = min(int(alarm["clock"]), d["to"])
                if state != -1:
                    seconds += max(0, clock - since)
                state, since = int(alarm["value"]), clock
            if state != -1:
                seconds += max(0, min(d["to"], end_of_data) - since)
            out.append({"day": d["day"], "minutes": round(seconds / 60)})
        return out
    return cache.get(f"downtime:{code}", load, [], ttl=120)


# --------------------------------------------------------------------- Golden Config
def compliance() -> dict[str, dict]:
    """Golden Config results per device id: compliant or the rules that are not."""
    def load():
        rows = clients.nautobot().list("plugins/golden-config/config-compliance/", depth=1)
        out: dict[str, dict] = {}
        for r in rows:
            device = (r.get("device") or {}).get("id")
            entry = out.setdefault(device, {"rules": 0, "failed": []})
            entry["rules"] += 1
            if not r.get("compliance"):
                entry["failed"].append((r.get("rule") or {}).get("display") or "rule")
        return out
    return cache.get("compliance", load, {})


def backups() -> dict[str, dict]:
    """Golden Config backup dates per device id."""
    def load():
        out = {}
        for r in clients.nautobot().list("plugins/golden-config/golden-config/", depth=1):
            device = (r.get("device") or {}).get("id")
            out[device] = {"last_success": iso_time(r.get("backup_last_success_date")),
                           "last_attempt": iso_time(r.get("backup_last_attempt_date")),
                           "compliance_checked": iso_time(r.get("compliance_last_success_date"))}
        return out
    return cache.get("backups", load, {})


# ------------------------------------------------------------------------- Semaphore
def limit_allowed(template: dict) -> bool:
    """Semaphore runs a template on the devices chosen for a task only when the template allows the limit
    to be overridden; otherwise it silently runs on the whole inventory."""
    return bool((template.get("task_params") or {}).get("allow_override_limit"))


_TASK_BY = re.compile(r"^MSP Console, ([^,]+), ((?:Ticket|Change) #\d+)", re.I)
_TASK_ITEM = re.compile(r"\b((?:ticket|change))\s*#\s*(\d+)", re.I)


def _task(t: dict, templates: dict, project: int, code: str) -> dict:
    message = t.get("message") or ""
    console = _TASK_BY.match(message)
    item = _TASK_ITEM.search(message)
    return {
        "id": t["id"], "status": t.get("status", ""),
        "template": t.get("tpl_alias") or (templates.get(t.get("template_id")) or {}).get("name", "?"),
        "created": iso_time(t.get("created")), "finished": iso_time(t.get("end")), "message": message,
        "devices": list((t.get("params") or {}).get("limit") or []) or [x for x in (t.get("limit") or "").split(",") if x],
        "by": console.group(1) if console else t.get("user_name") or "msp-glue",
        "item": f"{item.group(1).title()} #{item.group(2)}" if item else "", "customer": code,
        "link": f"{settings.public_url('semaphore')}/project/{project}/history?t={t['id']}",
    }


def automation(code: str) -> dict:
    """Templates and the most recent Semaphore tasks of one customer's project."""
    tenant = customer(code)
    empty = {"project_id": None, "templates": [], "tasks": []}
    if not tenant or not tenant.semaphore_project_id:
        return empty

    def load():
        sem = clients.semaphore()
        project = int(tenant.semaphore_project_id)
        templates = {t["id"]: t for t in sem.templates(project)}
        tasks = [_task(t, templates, project, code) for t in (sem.request("GET", f"project/{project}/tasks/last") or [])[:25]]
        return {"project_id": project,
                "templates": sorted(({"id": i, "name": t["name"], "description": t.get("description", ""),
                                      # Without this Semaphore ignores the device limit of a run (see actions.py)
                                      "limit": limit_allowed(t)}
                                     for i, t in templates.items()), key=lambda t: t["name"]),
                "tasks": tasks}
    return cache.get(f"automation:{code}", load, empty)


def last_runs() -> dict[str, dict]:
    """Most recent Semaphore task per customer (for the customers table)."""
    def load():
        sem = clients.semaphore()
        out = {}
        for tenant in customers():
            if not tenant.semaphore_project_id:
                continue
            tasks = sem.request("GET", f"project/{int(tenant.semaphore_project_id)}/tasks/last") or []
            if tasks:
                out[tenant.code] = {"status": tasks[0].get("status", ""), "when": iso_time(tasks[0].get("created")),
                                    "template": tasks[0].get("tpl_alias") or ""}
        return out
    return cache.get("last_runs", load, {})


# ---------------------------------------------------------------------------- msp-glue
def glue_jobs() -> dict:
    """msp-glue's queue, last error and recent jobs."""
    def load():
        session = make_session()
        response = session.get(f"{settings.glue_internal_url.rstrip('/')}/jobs", timeout=10,
                               headers={"Authorization": f"Bearer {settings.nautobot_token}"})
        response.raise_for_status()
        return response.json()
    return cache.get("glue_jobs", load, {"jobs": [], "queue": None, "last_error": None, "last_reconcile": None},
                     ttl=10)


# ------------------------------------------------------------------------------ health
def health() -> list[dict]:
    """Can the console reach every tool? Probes run at the same time with a short time-out."""
    def probe(name: str, func) -> dict:
        started = time.time()
        try:
            return {"tool": name, "ok": True, "detail": str(func()), "ms": int((time.time() - started) * 1000)}
        except Exception as exc:  # noqa: BLE001
            return {"tool": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:200], "ms": None}

    def load():
        probes = {
            "Nautobot": lambda: _short(clients.nautobot()).request("GET", "status/")["nautobot-version"],
            "Zabbix": lambda: _short(clients.zabbix()).version(),
            "GLPI": _glpi_alive,
            "Semaphore": _semaphore_alive,
            "Forgejo": lambda: _short(clients.forgejo()).request("GET", "version")["version"].split("+")[0],
            "Keycloak": _keycloak_alive,
        }
        if settings.grafana_url:
            probes["Grafana"] = _grafana_alive
        futures = {name: _probes.submit(probe, name, func) for name, func in probes.items()}
        out = []
        for name, future in futures.items():
            try:
                out.append(future.result(timeout=3 * PROBE_SECONDS))
            except Exception:  # noqa: BLE001 - a probe that hangs beyond its own time-out
                out.append({"tool": name, "ok": False, "detail": f"no answer within {3 * PROBE_SECONDS} s", "ms": None})
        return out
    return cache.get("health", load, [], ttl=30)


def _short(client):
    """The same client with a short time-out and no retries, for health probes."""
    session = make_session(retries=0)
    session.headers.update(client.session.headers)
    client.session, client.timeout = session, PROBE_SECONDS
    return client


def _glpi_alive() -> str:
    # The version needs a full API session, so it is read once an hour; the probe itself is cheap.
    response = make_session(retries=0).get(f"{settings.glpi_url.rstrip('/')}/apirest.php/", timeout=PROBE_SECONDS)
    if response.status_code >= 500:
        raise RuntimeError(f"HTTP {response.status_code}")
    return cache.get("glpi_version", _glpi_version, "?", ttl=3600)


def _glpi_version() -> str:
    with clients.glpi() as g:
        return g.request("GET", "getGlpiConfig")["cfg_glpi"]["version"]


def _semaphore_alive() -> str:
    # /api/info starts Ansible to report its version, so it is read once an hour; /api/ping is the probe.
    sem = _short(clients.semaphore())
    if sem.request("GET", "ping") != "pong":
        raise RuntimeError("no pong")
    return cache.get("semaphore_version",
                     lambda: clients.semaphore().request("GET", "info").get("version", "?").split("-")[0], "?",
                     ttl=3600)


def _keycloak_alive() -> str:
    # The version needs Keycloak's admin interface, which the console has no account for; the realm answering
    # its sign-in configuration is what counts.
    url = f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}/.well-known/openid-configuration"
    response = make_session(retries=0).get(url, timeout=PROBE_SECONDS)
    response.raise_for_status()
    return f"realm {settings.keycloak_realm}"


def _grafana_alive() -> str:
    response = make_session(retries=0).get(f"{settings.grafana_url.rstrip('/')}/api/health", timeout=PROBE_SECONDS)
    response.raise_for_status()
    return response.json().get("version", "?")


def grafana_org(tenant: Tenant) -> int | None:
    if not (settings.grafana_url and settings.grafana_admin_password):
        return None

    def load():
        from ..clients.grafana import Grafana
        found = Grafana(settings.grafana_url, settings.grafana_admin_user, settings.grafana_admin_password) \
            .request("GET", f"orgs/name/{tenant.name}", ok404=True)
        return int(found["id"]) if found else None
    return cache.get(f"grafana_org:{tenant.code}", load, None, ttl=3600)


def sites(code: str) -> list[dict]:
    """The customer's sites: Nautobot locations of its devices, and locations that belong to the tenant."""
    tenant = customer(code)

    def load():
        found = {}
        if tenant:
            for loc in clients.nautobot().list("dcim/locations/", tenant_id=tenant.id, depth=1):
                found[loc["id"]] = {"id": loc["id"], "name": loc["name"],
                                    "region": ((loc.get("parent") or {}).get("name") or "")}
        for d in devices():
            if d["customer"] == code and d["site_id"] and d["site_id"] not in found:
                found[d["site_id"]] = {"id": d["site_id"], "name": d["site"], "region": d["region"]}
        return sorted(found.values(), key=lambda s: s["name"])
    return cache.get(f"sites:{code}", load, [])


# ============================================================================ pages
def customer_rows() -> list[dict]:
    """One row per customer for the operations page."""
    devs, hs, probs, tickets = devices(), hosts(), problems(), open_tickets()
    comp, backs, runs = compliance(), backups(), last_runs()
    rows = []
    for t in customers():
        mine = [d for d in devs if d["customer"] == t.code]
        active = [d for d in mine if d["status"] == "Active"]
        states = Counter(hs[d["id"]]["state"] for d in active if d["id"] in hs)
        unmonitored = [d for d in active if d["id"] not in hs and d["monitoring"]]
        sev = Counter(p["severity"] for p in probs if p["customer"] == t.code)
        checked = [comp[d["id"]] for d in active if d["id"] in comp]
        backup_times = [backs[d["id"]]["last_success"] for d in active if d["id"] in backs and backs[d["id"]]["last_success"]]
        in_scope = sum(1 for d in active if d["gc_scope"])
        failed_backups = sum(1 for d in active if d["id"] in backs and backs[d["id"]]["last_attempt"]
                             and (not backs[d["id"]]["last_success"]
                                  or backs[d["id"]]["last_attempt"] > backs[d["id"]]["last_success"] + timedelta(minutes=5)))
        rows.append({
            "code": t.code, "name": t.name, "tier": t.tier, "regulated": t.regulated,
            "customer_id": t.customer_id, "contact_email": t.contact_email,
            "provisioning": t.custom_fields.get("msp_provisioning_status") or "pending",
            "provisioning_message": t.custom_fields.get("msp_provisioning_message") or "",
            "devices": len(mine), "active": len(active), "up": states.get("up", 0), "down": states.get("down", 0),
            "down_in_maintenance": sum(1 for d in active if d["id"] in hs and hs[d["id"]]["state"] == "down"
                                       and hs[d["id"]]["maintenance"]),
            "unknown": states.get("unknown", 0), "unmonitored": len(unmonitored),
            "problems": sum(sev.values()), "worst": max(sev) if sev else None,
            "high": sev.get(4, 0) + sev.get(5, 0),
            "unacknowledged": sum(1 for p in probs if p["customer"] == t.code and not p["acknowledged"]),
            "tickets": sum(1 for x in tickets if x["customer"] == t.code),
            "late": sum(1 for x in tickets if x["customer"] == t.code and x["late"]),
            "compliant": sum(1 for c in checked if not c["failed"]), "checked": len(checked), "in_scope": in_scope,
            "last_backup": max(backup_times) if backup_times else None, "backup_failed": failed_backups,
            "last_run": runs.get(t.code),
        })
    rows.sort(key=lambda r: (-(r["worst"] or -1), -r["late"], r["name"]))
    return rows


def service_rows(start: datetime | None = None) -> list[dict]:
    """One row per customer for the managers' service overview: this month, or the month from start."""
    current = start is None or start >= month_start()
    start = start or month_start()
    end = None if current else next_month(start)
    avail, month, devs, comp, backs = availability(), ticket_stats(start, end), devices(), compliance(), backups()
    tickets = open_tickets()
    day_ago = now() - timedelta(hours=26)
    key = start.strftime("%Y-%m")
    rows = []
    for t in customers():
        active = [d for d in devs if d["customer"] == t.code and d["status"] == "Active"]
        net = [d for d in active if d["gc_scope"]]   # devices Golden Config looks after
        checked = [comp[d["id"]] for d in active if d["id"] in comp]
        fresh = [d for d in net if d["id"] in backs and backs[d["id"]]["last_success"]
                 and backs[d["id"]]["last_success"] > day_ago]
        a = avail.get(t.code, {})
        period = next((p for p in a.get("periods", []) if p["month"] == key), {})
        m = month.get(t.code, {})
        sli = period.get("sli") if not current else a.get("sli")
        rows.append({
            "code": t.code, "name": t.name, "tier": t.tier, "regulated": t.regulated,
            "slo": a.get("slo"), "sli": sli, "previous": a.get("previous"),
            "downtime": period.get("downtime") if not current else a.get("downtime"),
            "budget": a.get("budget"), "period_start": period.get("start") or start,
            "period_end": period.get("end") or next_month(start),
            "history": [{"month": p["month"], "sli": p["sli"]} for p in a.get("periods", [])],
            "met": None if sli is None or a.get("slo") is None else sli >= a["slo"],
            "opened": m.get("opened", 0), "solved": m.get("solved", 0), "month_late": m.get("late", 0),
            "own_late": m.get("own_late", 0), "resolve_late": m.get("resolve_late", 0),
            "mttr": m.get("mttr_hours"),
            "open": sum(1 for x in tickets if x["customer"] == t.code) if current else 0,
            "open_late": sum(1 for x in tickets if x["customer"] == t.code and x["late"]) if current else 0,
            "compliance": round(100 * sum(1 for c in checked if not c["failed"]) / len(checked)) if checked else None,
            "backup": round(100 * len(fresh) / len(net)) if net else None,
            "compliant_n": sum(1 for c in checked if not c["failed"]), "checked_n": len(checked),
            "fresh_n": len(fresh), "net_n": len(net),
            "active": len(active),
        })
    rows.sort(key=lambda r: (r["met"] is not False, r["name"]))
    return rows


def errors() -> dict[str, str]:
    return dict(cache.errors)
