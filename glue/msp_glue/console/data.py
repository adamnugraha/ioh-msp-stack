"""Read-only views over every tool for the MSP Console.

Each loader asks one tool for everything the pages need and keeps the answer for
CONSOLE_CACHE_SECONDS, so ten engineers refreshing the same page cost one round of API calls.
A tool that does not answer never breaks a page: its loader returns empty data and the health
strip shows which tool failed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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

    def clear(self, *keys: str) -> None:
        for key in keys or list(self._data):
            self._data.pop(key, None)

    def clear_prefix(self, prefix: str) -> None:
        self.clear(*[k for k in list(self._data) if k.startswith(prefix)])


cache = _Cache()
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="console-load")
# The health probes have threads of their own: queued behind page loaders that are themselves waiting
# for the health answer, they would never run.
_probes = ThreadPoolExecutor(max_workers=5, thread_name_prefix="console-probe")
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


def _glpi_time(value) -> datetime | None:
    """GLPI returns local date-times as text, for example 2026-10-05 23:52:35."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz())
    except ValueError:
        return None


def _iso_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(tz())
    except ValueError:
        return None


# ============================================================================ loaders
def customers() -> list[Tenant]:
    return cache.get("customers", lambda: [t for t in managed_tenants() if t.valid], [])


def customer(code: str) -> Tenant | None:
    return next((t for t in customers() if t.code == code), None)


DEVICES_QUERY = """
query ($group: [String]) {
  devices(tenant_group: $group) {
    id name serial
    status { name } role { name } platform { name network_driver } location { name }
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
            status = (d.get("status") or {}).get("name", "")
            platform = (d.get("platform") or {}).get("name", "")
            ip = host_ip(d.get("primary_ip4")) or host_ip(d.get("primary_ip6"))
            rows.append({
                "id": d["id"], "name": d["name"], "serial": d.get("serial") or "",
                "status": status, "role": (d.get("role") or {}).get("name", ""), "platform": platform,
                "driver": (d.get("platform") or {}).get("network_driver", ""),
                "site": (d.get("location") or {}).get("name", ""),
                "customer": (tenant_cf.get("msp_code") or "").lower(), "ip": ip,
                # Golden Config looks after active network devices with an address and a supported platform
                "gc_scope": status == "Active" and bool(ip) and platform in gc_platforms,
                "monitoring": (d.get("custom_fields") or {}).get("msp_monitoring") is not False,
                "drift_ticket": (d.get("custom_fields") or {}).get("msp_drift_ticket_id"),
            })
        return rows
    return cache.get("devices", load, [])


def hosts() -> dict[str, dict]:
    """Zabbix hosts msp-glue manages, keyed by Nautobot device id."""
    def load():
        found = clients.zabbix().call("host.get", {
            "output": ["hostid", "host", "name", "status"],
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
                "error": (main[0].get("error") if main else "") or "",
            }
        return out
    return cache.get("hosts", load, {})


def problems() -> list[dict]:
    """Open Zabbix problems on managed hosts, worst first, with their GLPI ticket."""
    def load():
        rows = clients.zabbix().call("problem.get", {
            "output": ["eventid", "objectid", "clock", "name", "severity", "acknowledged", "suppressed"],
            "selectTags": "extend", "recent": False,
            "tags": [{"tag": "managed_by", "value": "msp-glue", "operator": 1}],
            "sortfield": ["eventid"], "sortorder": "DESC",
        })
        zbx = settings.public_url("zabbix")
        out = []
        for p in rows:
            tags = {}
            for t in p.get("tags", []):
                tags.setdefault(t["tag"], t["value"])
            started = datetime.fromtimestamp(int(p["clock"]), tz())
            severity = int(p["severity"])
            out.append({
                "eventid": p["eventid"], "name": p["name"], "severity": severity,
                "severity_name": SEVERITIES.get(severity, "?"), "started": started,
                "age": duration((now() - started).total_seconds()),
                "acknowledged": str(p["acknowledged"]) == "1", "suppressed": str(p.get("suppressed")) == "1",
                "customer": tags.get("tenant", ""), "customer_name": tags.get("tenant_name", ""),
                "device": tags.get("device", ""), "site": tags.get("site", ""),
                "ticket_id": tags.get("__zbx_glpi_ticket_id", ""),
                "zabbix_link": f"{zbx}/tr_events.php?triggerid={p['objectid']}&eventid={p['eventid']}",
            })
        out.sort(key=lambda r: (r["acknowledged"], -r["severity"], r["started"]))
        return out
    return cache.get("problems", load, [])


_TICKET_FIELDS = {2: "id", 1: "name", 12: "status", 3: "priority", 80: "entity", 15: "opened", 18: "due",
                  8: "group", 5: "technician", 82: "late", 14: "type", 159: "own_late"}


def _ticket_search(criteria: list[tuple[int, str, object]], fields: dict[int, str] | None = None) -> list[dict]:
    fields = fields or _TICKET_FIELDS
    params: dict = {"range": "0-999"}
    for i, (field, searchtype, value) in enumerate(criteria):
        if i:
            params[f"criteria[{i}][link]"] = "AND"
        params.update({f"criteria[{i}][field]": field, f"criteria[{i}][searchtype]": searchtype,
                       f"criteria[{i}][value]": value})
    for i, field in enumerate(fields):
        params[f"forcedisplay[{i}]"] = field
    with clients.glpi() as g:
        body = g.request("GET", "search/Ticket", params=params) or {}
    return [{name: row.get(str(field)) for field, name in fields.items()} for row in body.get("data") or []]


def _customer_by_entity() -> dict[str, str]:
    """GLPI entity complete name ("... > IOH Managed Services > PT ACME") -> customer code."""
    return {t.name: t.code for t in customers()}


def _entity_customer(entity: str, names: dict[str, str]) -> str:
    leaf = (entity or "").split(">")[-1].strip()
    return names.get(leaf, "")


def open_tickets() -> list[dict]:
    """Tickets that are not solved or closed, late ones first."""
    def load():
        names = _customer_by_entity()
        glpi = settings.public_url("glpi")
        rows = []
        for r in _ticket_search([(12, "equals", "notold")]):
            due = _glpi_time(r.get("due"))
            rows.append({
                "id": int(r["id"]), "name": r.get("name") or "", "status": TICKET_STATUS.get(int(r.get("status") or 0), "?"),
                "priority": int(r.get("priority") or 3), "priority_name": PRIORITY.get(int(r.get("priority") or 3), "?"),
                "customer": _entity_customer(r.get("entity"), names), "opened": _glpi_time(r.get("opened")),
                "due": due, "late": str(r.get("late")) == "1" or bool(due and due < now()),
                "group": r.get("group") or "", "technician": r.get("technician") or "",
                "type": "Incident" if str(r.get("type")) == "1" else "Request",
                "link": f"{glpi}/front/ticket.form.php?id={int(r['id'])}",
            })
        rows.sort(key=lambda t: (not t["late"], t["due"] or now() + timedelta(days=365), -t["priority"]))
        return rows
    return cache.get("open_tickets", load, [])


def month_start() -> datetime:
    return now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_tickets() -> dict[str, dict]:
    """Per customer, tickets opened this month: solved, service level breached (time to own or time to
    resolve exceeded), mean time to resolve."""
    def load():
        names = _customer_by_entity()
        fields = {2: "id", 12: "status", 80: "entity", 15: "opened", 17: "solved", 82: "late", 159: "own_late",
                  14: "type"}
        stats: dict[str, dict] = defaultdict(lambda: {"opened": 0, "solved": 0, "late": 0, "own_late": 0,
                                                      "resolve_late": 0, "resolve_hours": []})
        for r in _ticket_search([(15, "morethan", month_start().strftime("%Y-%m-%d %H:%M:%S"))], fields):
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
            opened, solved = _glpi_time(r.get("opened")), _glpi_time(r.get("solved"))
            if solved and int(r.get("status") or 0) >= STATUS_SOLVED:
                s["solved"] += 1
                if opened:
                    s["resolve_hours"].append((solved - opened).total_seconds() / 3600)
        out = {}
        for code, s in stats.items():
            hours = s.pop("resolve_hours")
            out[code] = {**s, "mttr_hours": round(sum(hours) / len(hours), 1) if hours else None}
        return out
    return cache.get("month_tickets", load, {})


def availability() -> dict[str, dict]:
    """Availability per customer for this month and last month, from the Zabbix SLA msp-glue creates
    ("Customer <code> availability", monthly period)."""
    def cell(report: dict, index: int) -> dict | None:
        rows = report.get("sli") or []
        if len(rows) < abs(index) or not rows[index]:
            return None
        value = rows[index][0]
        return value if float(value.get("sli", -1)) >= 0 else None   # -1: before the SLA started

    def load():
        z = clients.zabbix()
        out = {}
        for sla in z.call("sla.get", {"output": ["slaid", "name", "slo"]}):
            name = sla["name"]
            if not (name.startswith("Customer ") and name.endswith(" availability")):
                continue
            code = name[len("Customer "):-len(" availability")]
            report = z.call("sla.getsli", {"slaid": sla["slaid"], "periods": 2})   # oldest period first
            current, previous = cell(report, -1), cell(report, 0) if len(report.get("sli") or []) > 1 else None
            out[code] = {
                "slo": float(sla["slo"]),
                "sli": round(float(current["sli"]), 3) if current else None,
                "downtime": int(current["downtime"]) if current else None,
                "budget": int(current["error_budget"]) if current else None,
                "previous": round(float(previous["sli"]), 3) if previous else None,
            }
        return out
    return cache.get("availability", load, {})


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
            out[device] = {"last_success": _iso_time(r.get("backup_last_success_date")),
                           "last_attempt": _iso_time(r.get("backup_last_attempt_date")),
                           "compliance_checked": _iso_time(r.get("compliance_last_success_date"))}
        return out
    return cache.get("backups", load, {})


def limit_allowed(template: dict) -> bool:
    """Semaphore runs a template on the devices chosen for a task only when the template allows the limit
    to be overridden; otherwise it silently runs on the whole inventory."""
    return bool((template.get("task_params") or {}).get("allow_override_limit"))


def automation(code: str) -> dict:
    """Templates and the most recent Semaphore tasks of one customer's project."""
    tenant = customer(code)
    if not tenant or not tenant.semaphore_project_id:
        return {"project_id": None, "templates": [], "tasks": []}

    def load():
        sem = clients.semaphore()
        project = int(tenant.semaphore_project_id)
        templates = {t["id"]: t for t in sem.templates(project)}
        tasks = []
        for t in (sem.request("GET", f"project/{project}/tasks/last") or [])[:15]:
            tasks.append({"id": t["id"], "status": t.get("status", ""),
                          "template": (templates.get(t.get("template_id")) or {}).get("name", "?"),
                          "created": _iso_time(t.get("created")), "message": t.get("message") or "",
                          "link": f"{settings.public_url('semaphore')}/project/{project}/history?t={t['id']}"})
        return {"project_id": project,
                "templates": sorted(({"id": i, "name": t["name"], "description": t.get("description", ""),
                                      # Without this Semaphore ignores the device limit of a run (see actions.py)
                                      "limit": limit_allowed(t)}
                                     for i, t in templates.items()), key=lambda t: t["name"]),
                "tasks": tasks}
    return cache.get(f"automation:{code}", load, {"project_id": None, "templates": [], "tasks": []})


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
                out[tenant.code] = {"status": tasks[0].get("status", ""), "when": _iso_time(tasks[0].get("created"))}
        return out
    return cache.get("last_runs", load, {})


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
        }
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


def grafana_org(tenant: Tenant) -> int | None:
    if not (settings.grafana_url and settings.grafana_admin_password):
        return None

    def load():
        from ..clients.grafana import Grafana
        found = Grafana(settings.grafana_url, settings.grafana_admin_user, settings.grafana_admin_password) \
            .request("GET", f"orgs/name/{tenant.name}", ok404=True)
        return int(found["id"]) if found else None
    return cache.get(f"grafana_org:{tenant.code}", load, None, ttl=3600)


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
        rows.append({
            "code": t.code, "name": t.name, "tier": t.tier, "regulated": t.regulated,
            "provisioning": t.custom_fields.get("msp_provisioning_status") or "pending",
            "provisioning_message": t.custom_fields.get("msp_provisioning_message") or "",
            "devices": len(mine), "active": len(active), "up": states.get("up", 0), "down": states.get("down", 0),
            "unknown": states.get("unknown", 0), "unmonitored": len(unmonitored),
            "problems": sum(sev.values()), "worst": max(sev) if sev else None,
            "high": sev.get(4, 0) + sev.get(5, 0),
            "tickets": sum(1 for x in tickets if x["customer"] == t.code),
            "late": sum(1 for x in tickets if x["customer"] == t.code and x["late"]),
            "compliant": sum(1 for c in checked if not c["failed"]), "checked": len(checked), "in_scope": in_scope,
            "last_backup": max(backup_times) if backup_times else None,
            "last_run": runs.get(t.code),
        })
    rows.sort(key=lambda r: (-(r["worst"] or -1), -r["late"], r["name"]))
    return rows


def service_rows() -> list[dict]:
    """One row per customer for the managers' service overview."""
    avail, month, devs, comp, backs = availability(), month_tickets(), devices(), compliance(), backups()
    tickets = open_tickets()
    day_ago = now() - timedelta(hours=26)
    rows = []
    for t in customers():
        active = [d for d in devs if d["customer"] == t.code and d["status"] == "Active"]
        net = [d for d in active if d["gc_scope"]]   # devices Golden Config looks after
        checked = [comp[d["id"]] for d in active if d["id"] in comp]
        fresh = [d for d in net if d["id"] in backs and backs[d["id"]]["last_success"]
                 and backs[d["id"]]["last_success"] > day_ago]
        a = avail.get(t.code, {})
        m = month.get(t.code, {})
        rows.append({
            "code": t.code, "name": t.name, "tier": t.tier, "regulated": t.regulated,
            "slo": a.get("slo"), "sli": a.get("sli"), "previous": a.get("previous"),
            "downtime": a.get("downtime"), "budget": a.get("budget"),
            "met": None if a.get("sli") is None else a["sli"] >= a["slo"],
            "opened": m.get("opened", 0), "solved": m.get("solved", 0), "month_late": m.get("late", 0),
            "own_late": m.get("own_late", 0), "resolve_late": m.get("resolve_late", 0),
            "mttr": m.get("mttr_hours"),
            "open": sum(1 for x in tickets if x["customer"] == t.code),
            "open_late": sum(1 for x in tickets if x["customer"] == t.code and x["late"]),
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
