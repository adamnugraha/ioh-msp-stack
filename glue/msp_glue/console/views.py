"""The console's JSON shapes: what each /api/v1 answer holds, built from the loaders.

The field names and types follow glue/console-ui/src/lib/api/types.ts exactly; the React pages are
written against that file, and the preview's sample data follows it too.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from ..settings import settings
from . import actions, data, detail, store, tools
from .auth import User, csrf_token

SOURCES = {
    "customers": "Nautobot customers", "devices": "Nautobot devices", "hosts": "Zabbix hosts",
    "problems": "Zabbix problems", "open_tickets": "GLPI tickets", "tickets_from": "GLPI ticket statistics",
    "availability": "Zabbix availability", "compliance": "Golden Config compliance",
    "backups": "Golden Config backups", "last_runs": "Semaphore tasks", "glue_jobs": "msp-glue jobs",
    "changes": "GLPI changes", "maintenances": "Zabbix maintenance", "automation": "Semaphore project",
    "items": "Zabbix items", "interfaces": "Nautobot interfaces", "downtime": "Zabbix events",
    "sites": "Nautobot locations", "rules": "Golden Config rules", "zabbix_users": "Zabbix users",
}


def iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def freshness(*keys: str) -> dict:
    """When the data on a page was read, and which of its sources did not answer."""
    errors = []
    for key, error in data.errors().items():
        base = key.split(":")[0]
        if keys and base not in keys and key not in keys:
            continue
        loaded = data.cache.loaded(key)
        errors.append({"source": SOURCES.get(base, key), "error": error, "since": iso(loaded)})
    return {"loaded": iso(data.cache.oldest(*keys) or data.now()), "errors": errors}


# ============================================================================== who
def me(user: User, request, investigate_on: bool) -> dict:
    row = store.load_session(user.sid) if user.sid else None
    ends = datetime.fromtimestamp(row["expires"], data.tz()) if row else data.now() + timedelta(hours=10)
    return {
        "email": user.email, "name": user.name, "roles": user.role_names,
        "canAct": user.can_act, "canRun": user.can_run, "isManager": user.is_manager,
        "csrf": csrf_token(request), "sessionEnds": iso(ends),
        "accountUrl": f"{settings.public_url('keycloak')}/realms/{settings.keycloak_realm}/account",
        "timezone": str(data.tz()), "platformDomain": settings.msp_domain, "investigate": investigate_on,
    }


def health() -> list[dict]:
    probes = {p["tool"]: p for p in data.health()}
    out = []
    for tool in tools.TOOLS:
        if tool == "Grafana" and not settings.grafana_url:
            continue
        p = probes.get(tool, {"ok": False, "detail": "not checked yet", "ms": None})
        found = re.match(r"^v?(\d+(?:\.\d+)+)", str(p.get("detail") or "")) if p.get("ok") else None
        version = found.group(1) if found else None
        out.append({"tool": tool, "url": tools.url(tool), "purpose": tools.purpose(tool), "ok": bool(p.get("ok")),
                    "version": version, "ms": p.get("ms"), "detail": p.get("detail") or "",
                    "signIn": tools.sign_in(tool)})
    return out


# ========================================================================= customers
def _sla(code: str) -> dict | None:
    a = data.availability().get(code)
    if not a:
        return None
    start = data.month_start()
    month_seconds = (data.next_month(start) - start).total_seconds()
    return {"slo": a["slo"], "sli": a["sli"], "previous": a["previous"], "downtime": int(a.get("downtime") or 0),
            "allowed": int(month_seconds * (100 - a["slo"]) / 100)}


def _changes_today(rows: list[dict]) -> list[dict]:
    """Changes whose window touches today (until 06:00 tomorrow) and that are not finished."""
    moment = data.now()
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1, hours=6)
    return [c for c in rows if c["planned_start"] and c["planned_end"] and c["status"] not in data.CHANGE_DONE
            and c["planned_end"] >= moment and c["planned_start"] <= end]


def summaries() -> list[dict]:
    data.prefetch(data.customers, data.devices, data.hosts, data.problems, data.open_tickets, data.compliance,
                  data.backups, data.last_runs, data.availability, data.changes, data.maintenances)
    rows = data.customer_rows()
    changes, maint = data.changes(), data.maintenances()
    today = _changes_today(changes)
    out = []
    for r in rows:
        run = r["last_run"]
        out.append({
            "code": r["code"], "name": r["name"], "tier": r["tier"], "regulated": r["regulated"],
            "provisioning": r["provisioning"], "provisioningMessage": r["provisioning_message"],
            "customerId": r["customer_id"], "contactEmail": r["contact_email"],
            "devices": r["devices"], "active": r["active"], "up": r["up"], "down": r["down"],
            "downInMaintenance": r["down_in_maintenance"], "unknown": r["unknown"], "unmonitored": r["unmonitored"],
            "problems": r["problems"], "worst": r["worst"], "high": r["high"], "unacknowledged": r["unacknowledged"],
            "tickets": r["tickets"], "late": r["late"],
            "changesToday": sum(1 for c in today if c["customer"] == r["code"]),
            "maintenanceNow": sum(1 for m in maint if m["customer"] == r["code"] and m["state"] == "active"),
            "compliant": r["compliant"], "checked": r["checked"], "inScope": r["in_scope"],
            "lastBackup": iso(r["last_backup"]), "backupFailed": r["backup_failed"],
            "lastRun": {"status": run["status"], "when": iso(run["when"]), "template": run.get("template", "")} if run else None,
            "sla": _sla(r["code"]),
        })
    return out


def _slug(text: str) -> str:
    """Django's slugify, as Golden Config uses it in backup_path_template (mappings.yaml)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-_")


def device(d: dict, hosts: dict, comp: dict, backs: dict) -> dict:
    host = hosts.get(d["id"])
    check = comp.get(d["id"])
    back = backs.get(d["id"])
    links = {"nautobot": f"{settings.public_url('nautobot')}/dcim/devices/{d['id']}/"}
    if host:
        links["zabbix"] = detail.zabbix_host_link(host["hostid"])
    if d["gc_scope"]:
        prefix = settings.mappings.get("golden_config", {}).get("backup_repo_prefix", "backups-")
        links["backups"] = (f"{settings.public_url('forgejo')}/{settings.forgejo_org}/{prefix}{d['customer']}"
                            f"/src/branch/main/{_slug(d['site'])}/{d['name']}.cfg")
    if d["drift_ticket"]:
        links["driftTicket"] = f"{settings.public_url('glpi')}/front/ticket.form.php?id={int(d['drift_ticket'])}"
    return {
        "id": d["id"], "name": d["name"], "serial": d["serial"], "status": d["status"] or "Active",
        "role": detail.role_of(d["role"]), "platform": d["platform"], "model": d["model"], "site": d["site"],
        "customer": d["customer"], "ip": d["ip"], "gcScope": d["gc_scope"], "monitoring": d["monitoring"],
        "driftTicket": int(d["drift_ticket"]) if d["drift_ticket"] else None,
        "host": {"hostid": host["hostid"], "state": host["state"], "error": host["error"],
                 "maintenance": host["maintenance"]} if host else None,
        "check": {"rules": check["rules"], "failed": check["failed"],
                  "checked": iso((back or {}).get("compliance_checked"))} if check else None,
        "backup": {"lastSuccess": iso(back["last_success"]), "lastAttempt": iso(back["last_attempt"])} if back else None,
        "links": links,
    }


def _device_order(d: dict):
    return (d["status"] != "Active", d["site"], d["name"])


def problem(p: dict) -> dict:
    ticket = str(p.get("ticket_id") or "")
    return {
        "eventid": p["eventid"], "name": p["name"], "severity": p["severity"], "started": iso(p["started"]),
        "acknowledged": p["acknowledged"], "suppressed": p["suppressed"], "customer": p["customer"],
        "customerName": p["customer_name"], "device": p["device"], "site": p["site"],
        "ticketId": int(ticket) if ticket.isdigit() else None, "ticketLink": p.get("ticket_link"),
        "opdata": p.get("opdata") or "", "link": p["zabbix_link"],
        "updates": [{"at": iso(u["at"]), "who": u["who"], "message": u["message"]} for u in p.get("updates") or []],
    }


def ticket(t: dict) -> dict:
    return {"id": t["id"], "name": t["name"], "type": t["type"], "status": t["status"], "priority": t["priority"],
            "customer": t["customer"], "opened": iso(t["opened"]), "due": iso(t["due"]), "late": t["late"],
            "group": t["group"], "technician": t["technician"], "source": t["source"], "device": t["device"],
            "link": t["link"]}


def change(c: dict) -> dict:
    return {"id": c["id"], "name": c["name"], "status": c["status"], "customer": c["customer"],
            "category": c["category"], "template": c["template"], "plannedStart": iso(c["planned_start"]),
            "plannedEnd": iso(c["planned_end"]), "risk": c["risk"], "devices": c["devices"],
            "requester": c["requester"], "maintenanceId": c["maintenance_id"], "link": c["link"]}


def maintenance(m: dict) -> dict:
    moment = data.now()
    state = "upcoming" if moment < m["start"] else "ended" if moment > m["end"] else "active"
    return {"id": m["id"], "name": m["name"], "customer": m["customer"], "devices": m["devices"],
            "start": iso(m["start"]), "end": iso(m["end"]), "collectData": m["collect_data"],
            "changeId": m["change_id"], "createdBy": m["created_by"], "state": state, "link": m["link"]}


def task(t: dict) -> dict:
    return {"id": t["id"], "template": t["template"], "status": t["status"] or "waiting", "created": iso(t["created"]),
            "finished": iso(t["finished"]), "message": t["message"], "devices": t["devices"], "by": t["by"],
            "item": t["item"], "customer": t["customer"], "link": t["link"]}


def automation(code: str) -> dict:
    a = data.automation(code)
    change_only = actions.change_only_templates()
    project = a["project_id"]
    return {
        "projectId": project,
        "templates": [{"id": t["id"], "name": t["name"], "description": t["description"], "limit": t["limit"],
                       "changeOnly": t["name"] in change_only} for t in a["templates"]],
        "tasks": [task(t) for t in a["tasks"]],
        "link": f"{settings.public_url('semaphore')}/project/{project}/history" if project else None,
    }


def audit_entry(e: dict) -> dict:
    return {"id": e["id"], "at": iso(e["at"]), "email": e["email"], "name": e["name"], "action": e["action"],
            "customer": e["customer"], "target": e["target"], "ticket": e["ticket"], "reason": e["reason"],
            "outcome": e["outcome"] or "unknown", "result": e["result"], "address": e["address"],
            "updated": iso(e["updated"])}


def customer_links(tenant) -> dict:
    nautobot, zabbix, glpi = settings.public_url("nautobot"), settings.public_url("zabbix"), settings.public_url("glpi")
    links = {"nautobot": f"{nautobot}/tenancy/tenants/{tenant.id}/"}
    if tenant.zabbix_hostgroup_id:
        links["zabbix"] = (f"{zabbix}/zabbix.php?action=problem.view&filter_set=1"
                           f"&groupids%5B%5D={tenant.zabbix_hostgroup_id}")
    if tenant.glpi_entity_id:
        links["glpi"] = (f"{glpi}/front/ticket.php?criteria%5B0%5D%5Bfield%5D=80&criteria%5B0%5D%5Bsearchtype%5D"
                         f"=equals&criteria%5B0%5D%5Bvalue%5D={tenant.glpi_entity_id}")
    if tenant.semaphore_project_id:
        links["semaphore"] = f"{settings.public_url('semaphore')}/project/{tenant.semaphore_project_id}/history"
    prefix = settings.mappings.get("golden_config", {}).get("backup_repo_prefix", "backups-")
    links["forgejo"] = f"{settings.public_url('forgejo')}/{settings.forgejo_org}/{prefix}{tenant.code}"
    org = data.grafana_org(tenant)
    if org:
        links["grafana"] = f"{settings.public_url('grafana')}/dashboards?orgId={org}"
    return links


# ============================================================================== pages
def overview(user: User) -> dict:
    data.prefetch(data.health, data.glue_jobs)
    customers = summaries()
    problems, tickets = data.problems(), data.open_tickets()
    moment = data.now()
    maint = [m for m in data.maintenances()
             if m["state"] == "active" or (m["state"] == "upcoming" and m["start"] - moment < timedelta(days=1))]
    return {
        "problems": [problem(p) for p in problems],
        "customers": customers,
        "tickets": [ticket(t) for t in tickets[:12]],
        "changesToday": [change(c) for c in _changes_today(data.changes())],
        "maintenance": [maintenance(m) for m in maint],
        "glue": glue() if user.can_act else None,
        "devicesTotal": len(data.devices()),
        "freshness": freshness("customers", "devices", "hosts", "problems", "open_tickets", "changes", "maintenances",
                               "compliance", "backups", "last_runs", "availability"),
    }


def device_index() -> list[dict]:
    hosts = data.hosts()
    names = data.customer_names()
    return [{"name": d["name"], "customer": d["customer"], "customerName": names.get(d["customer"], d["customer"]),
             "site": d["site"], "role": detail.role_of(d["role"]), "ip": d["ip"], "status": d["status"],
             "state": (hosts.get(d["id"]) or {}).get("state")} for d in data.devices() if d["customer"] in names]


def customer_detail(code: str) -> dict | None:
    tenant = data.customer(code)
    if not tenant:
        return None
    data.prefetch(lambda: data.automation(code), lambda: data.downtime_by_day(code), lambda: data.sites(code),
                  lambda: data.grafana_org(tenant))
    summary = next((s for s in summaries() if s["code"] == code), None)
    hosts, comp, backs = data.hosts(), data.compliance(), data.backups()
    mine = sorted((d for d in data.devices() if d["customer"] == code), key=_device_order)
    return {
        "summary": summary,
        "links": customer_links(tenant),
        "sites": data.sites(code),
        "devices": [device(d, hosts, comp, backs) for d in mine],
        "problems": [problem(p) for p in data.problems() if p["customer"] == code],
        "tickets": [ticket(t) for t in data.open_tickets() if t["customer"] == code],
        "changes": [change(c) for c in data.changes() if c["customer"] == code],
        "maintenance": [maintenance(m) for m in data.maintenances() if m["customer"] == code],
        "automation": automation(code),
        "audit": [audit_entry(e) for e in store.entries(limit=15, customer=code)],
        "downtimeByDay": data.downtime_by_day(code),
        "freshness": freshness("customers", "devices", "hosts", "problems", "open_tickets", "changes", "maintenances",
                               f"automation:{code}", f"downtime:{code}", f"sites:{code}"),
    }


def device_detail(code: str, name: str) -> dict | None:
    tenant = data.customer(code)
    d = next((x for x in data.devices() if x["customer"] == code and x["name"] == name), None)
    if not tenant or not d:
        return None
    data.prefetch(lambda: detail.interfaces(d), lambda: detail.backup_history(d, code),
                  lambda: detail.compliance_rules(d), lambda: data.automation(code))
    hosts, comp, backs = data.hosts(), data.compliance(), data.backups()
    return {
        "device": device(d, hosts, comp, backs),
        "customerName": tenant.name,
        "interfaces": detail.interfaces(d),
        "problems": [problem(p) for p in data.problems() if p["customer"] == code and p["device"] == name],
        "tickets": [ticket(t) for t in data.open_tickets() if t["customer"] == code and t["device"] == name],
        "tasks": [task(t) for t in data.automation(code)["tasks"] if name in t["devices"]],
        "backups": [{**b, "at": iso(b["at"])} for b in detail.backup_history(d, code)],
        "compliance": detail.compliance_rules(d) if d["gc_scope"] else [],
        "maintenance": [maintenance(m) for m in data.maintenances()
                        if m["customer"] == code and (m["devices"] is None or name in m["devices"])],
        "charts": detail.charts(d),
        "freshness": freshness("devices", "hosts", "problems", "open_tickets", "maintenances", f"automation:{code}"),
    }


def work() -> dict:
    data.prefetch(data.changes, data.maintenances)
    changes = data.changes()
    return {"changes": [change(c) for c in changes],
            "maintenance": [maintenance(m) for m in data.maintenances()],
            "freshness": freshness("changes", "maintenances")}


def _month_label(start: datetime) -> str:
    return start.strftime("%B %Y")


def service(month: str | None) -> dict:
    current = data.month_start()
    available = [current] + [data.month_start(i) for i in (1, 2)]
    selected = next((m for m in available if m.strftime("%Y-%m") == month), current)
    data.prefetch(data.customers, data.availability, lambda: data.ticket_stats(selected, None if selected == current
                                                                              else data.next_month(selected)),
                  data.devices, data.compliance, data.backups, data.open_tickets)
    rows = data.service_rows(selected)
    out_rows = []
    for r in rows:
        sla = None
        if r["slo"] is not None:
            month_seconds = (r["period_end"] - r["period_start"]).total_seconds()
            sla = {"slo": r["slo"], "sli": r["sli"], "previous": r["previous"], "downtime": int(r["downtime"] or 0),
                   "allowed": int(month_seconds * (100 - r["slo"]) / 100)}
        out_rows.append({
            "code": r["code"], "name": r["name"], "tier": r["tier"], "regulated": r["regulated"], "sla": sla,
            "met": r["met"], "history": r["history"], "opened": r["opened"], "solved": r["solved"],
            "breaches": r["own_late"] + r["resolve_late"], "ownLate": r["own_late"], "resolveLate": r["resolve_late"],
            "mttrHours": r["mttr"], "open": r["open"], "openLate": r["open_late"], "compliance": r["compliance"],
            "backup": r["backup"], "compliantN": r["compliant_n"], "checkedN": r["checked_n"], "freshN": r["fresh_n"],
            "netN": r["net_n"],
        })
    solved = [r for r in rows if r["mttr"] is not None]
    total_solved = sum(r["solved"] for r in solved)

    def ratio(part, whole):
        return round(100 * part / whole) if whole else None

    return {
        "month": selected.strftime("%Y-%m"),
        "label": _month_label(selected) + (" (so far)" if selected == current else ""),
        "monthsAvailable": [{"month": m.strftime("%Y-%m"), "label": _month_label(m) + (" (so far)" if m == current else "")}
                            for m in available],
        "rows": out_rows,
        "totals": {
            "customers": len(rows), "withSla": sum(1 for r in rows if r["met"] is not None),
            "met": sum(1 for r in rows if r["met"]), "opened": sum(r["opened"] for r in rows),
            "solved": sum(r["solved"] for r in rows), "breaches": sum(r["own_late"] + r["resolve_late"] for r in rows),
            "open": sum(r["open"] for r in rows), "openLate": sum(r["open_late"] for r in rows),
            "mttrHours": round(sum(r["mttr"] * r["solved"] for r in solved) / total_solved, 1) if total_solved else None,
            "compliance": ratio(sum(r["compliant_n"] for r in rows), sum(r["checked_n"] for r in rows)),
            "backup": ratio(sum(r["fresh_n"] for r in rows), sum(r["net_n"] for r in rows)),
        },
        "freshness": freshness("customers", "availability", "tickets_from", "devices", "compliance", "backups",
                               "open_tickets"),
    }


def service_csv_rows(month: str | None) -> list[dict]:
    report = service(month)
    out = []
    for r in report["rows"]:
        sla = r["sla"] or {}
        out.append({"month": report["month"], "code": r["code"], "name": r["name"], "tier": r["tier"],
                    "regulated": r["regulated"], "slo": sla.get("slo"), "sli": sla.get("sli"),
                    "downtime_seconds": sla.get("downtime"), "met": r["met"], "opened": r["opened"],
                    "solved": r["solved"], "breaches": r["breaches"], "own_late": r["ownLate"],
                    "resolve_late": r["resolveLate"], "mttr_hours": r["mttrHours"], "open": r["open"],
                    "open_late": r["openLate"], "compliance": r["compliance"], "backup": r["backup"]})
    return out


def audit(customer: str = "", email: str = "", action: str = "", outcome: str = "") -> dict:
    entries = store.entries(limit=500, customer=customer, email=email, action=action, outcome=outcome)
    return {"entries": [audit_entry(e) for e in entries],
            "choices": {c: store.distinct(c) for c in ("customer", "email", "action", "outcome")}}


def sessions() -> list[dict]:
    labels = {"msp-manager": "manager", "msp-engineer": "engineer", "msp-automation": "automation"}
    return [{"email": s["email"], "name": s["name"], "roles": [labels[r] for r in labels if r in s["roles"]],
             "since": iso(s["created"]), "lastSeen": iso(s["checked"]), "address": s["address"]}
            for s in store.active_sessions()]


def glue() -> dict:
    g = data.glue_jobs()
    error = g.get("last_error")
    jobs = []
    for j in g.get("jobs") or []:
        status = j.get("status") or "queued"
        jobs.append({"id": j.get("id"), "name": j.get("name", ""),
                     "status": status if status in ("queued", "running", "done", "failed") else "queued",
                     "started": iso(data.local_time(j.get("started") or j.get("queued"))),
                     "finished": iso(data.local_time(j.get("finished"))), "result": j.get("result") or "",
                     "by": j.get("requested_by") or "msp-glue"})
    return {"queue": int(g.get("queue") or 0), "lastReconcile": iso(data.local_time(g.get("last_reconcile"))),
            "lastError": {"at": iso(data.local_time(g.get("last_error_at"))) or iso(data.now()), "message": error}
            if error else None,
            "jobs": jobs}
