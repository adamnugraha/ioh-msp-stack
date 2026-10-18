"""Changes made from the console.

Every action checks the person's role, writes an audit entry before it starts, and then works
through msp-glue's job queue or the tool's own API. Actions that take a while (re-sync, configuration
check, playbook) are followed by a background thread that writes the outcome to the same audit entry;
playbook results also go to the GLPI ticket or change the run was made for.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from .. import changes, clients
from ..clients.glpi import CHANGE_ACCEPTED, STATUS_SOLVED
from ..http import APIError, make_session
from ..settings import settings
from . import data, store
from .auth import User

log = logging.getLogger("msp_glue.console.actions")

MARKER = "[MSP Console]"
# GLPI change statuses after which nothing should run against the change any more:
# solved, closed, under review (observed), cancelled, refused.
CHANGE_FINISHED = {5, 6, 8, 13, 14}
# How long the console waits for a playbook's result before it says so in the ticket (patching takes a while).
PLAYBOOK_WAIT_SECONDS = 4 * 3600
_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RESERVED_VARIABLES = {"glpi_ticket_id", "glpi_change_id", "msp_console_user"}


class ActionError(Exception):
    """A refusal or failure to show to the person, in plain words."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Forbidden(ActionError):
    """The person's roles do not allow this."""

    def __init__(self, message: str):
        super().__init__(message, status=403)


@dataclass
class Result:
    message: str
    audit_id: int
    id: int | str | None = None

    def as_dict(self) -> dict:
        out = {"message": self.message, "auditId": self.audit_id}
        if self.id is not None:
            out["id"] = self.id
        return out


# =============================================================================
# Helpers
# =============================================================================
def _background(name: str, func, *args) -> None:
    def guard() -> None:
        try:
            func(*args)
        except Exception:  # noqa: BLE001
            log.exception("Console follow-up %s failed", name)
    threading.Thread(target=guard, daemon=True, name=name).start()


def _glue(method: str, path: str, user: User | None = None) -> dict:
    headers = {"Authorization": f"Bearer {settings.nautobot_token}"}
    if user:
        headers["X-Requested-By"] = user.email
    response = make_session(retries=0).request(method, f"{settings.glue_internal_url.rstrip('/')}/{path}",
                                               headers=headers, timeout=15)
    if response.status_code == 404:
        raise ActionError(response.json().get("detail", "not found"))
    response.raise_for_status()
    return response.json()


def _tenant(code: str):
    tenant = data.customer(code)
    if not tenant:
        raise ActionError(f"No customer with code {code}.")
    return tenant


def _duplicate(user: User, action: str, target: str, ticket: str | None = "", seconds: int = 60) -> bool:
    """The same person asked for the same thing a moment ago (a double click or a reloaded form).
    ticket=None: whatever ticket the earlier entry names (for things that create the ticket themselves)."""
    recent = store.entries(limit=20, email=user.email, action=action, since=time.time() - seconds)
    return any(e["target"] == target and (ticket is None or e["ticket"] == ticket) and e["outcome"] != "failed"
               for e in recent)


# =============================================================================
# Acknowledge a problem
# =============================================================================
def acknowledge(user: User, eventid: str, note: str, address: str = "") -> Result:
    if not user.can_act:
        raise Forbidden("Acknowledging problems needs the engineer role.")
    if not eventid.isdigit():
        raise ActionError("Unknown problem.")
    z = clients.zabbix()
    found = z.call("problem.get", {
        "eventids": [eventid], "output": ["eventid", "name", "acknowledged"], "selectTags": "extend",
        "tags": [{"tag": "managed_by", "value": "msp-glue", "operator": 1}]})
    if not found:
        raise ActionError("This problem is resolved already, or it is not on a device the platform manages.")
    problem = found[0]
    tags = {t["tag"]: t["value"] for t in problem.get("tags", [])}
    note = " ".join(note.split())[:500]
    already = str(problem["acknowledged"]) == "1"
    if already and not note:
        raise ActionError("This problem is acknowledged already. Write a note to add one.")
    ticket = tags.get("__zbx_glpi_ticket_id", "")
    entry = store.record(user.as_dict(), "acknowledge" if not already else "problem note",
                         customer=tags.get("tenant", ""), target=f"{tags.get('device', '?')}: {problem['name']}",
                         ticket=f"Ticket #{ticket}" if ticket else "", reason=note, outcome="started",
                         detail={"eventid": eventid}, address=address)
    message = f"{'Acknowledged' if not already else 'Note'} in the MSP Console by {user.name} ({user.email})"
    message += f": {note}" if note else "."
    try:
        # 2 = acknowledge, 4 = add a message. Zabbix passes the message on to the GLPI ticket.
        z.call("event.acknowledge", {"eventids": [eventid], "action": 4 if already else 6, "message": message})
    except APIError as exc:
        store.update(entry, "failed", str(exc))
        raise ActionError(f"Zabbix refused: {exc}") from exc
    store.update(entry, "done", message)
    data.cache.clear("problems")
    done = "Note added." if already else "Problem acknowledged."
    return Result(done + (f" Zabbix also adds it to GLPI ticket #{ticket}." if ticket else ""), entry)


# =============================================================================
# Re-sync a customer (tenant set-up, then its devices)
# =============================================================================
def resync(user: User, code: str, address: str = "") -> Result:
    if not user.can_act:
        raise Forbidden("Re-syncing customers needs the engineer role.")
    tenant = _tenant(code)
    if _duplicate(user, "re-sync", tenant.name, seconds=30):
        raise ActionError("A re-sync of this customer was just queued. Wait for it to finish.")
    entry = store.record(user.as_dict(), "re-sync", customer=code, target=tenant.name, outcome="queued",
                         address=address)
    try:
        jobs = [_glue("POST", f"run/tenant/{code}", user), _glue("POST", f"run/devices/{code}", user)]
    except Exception as exc:  # noqa: BLE001
        store.update(entry, "failed", f"msp-glue did not accept the job: {exc}")
        raise ActionError(f"msp-glue did not accept the job: {exc}") from exc
    wanted = {j["job"]: j["queued"] for j in jobs}
    store.update(entry, "queued", detail={"jobs": wanted})
    _background(f"follow re-sync {code}", _follow_glue_jobs, entry, wanted,
                ("customers", "devices", "hosts", "last_runs", f"automation:{code}"))
    return Result(f"Re-sync of {tenant.name} queued on msp-glue (jobs {', '.join(str(j) for j in wanted)}). "
                  "The result appears in the audit trail and under Integration jobs.", entry)


def _follow_glue_jobs(entry: int, wanted: dict, clear: tuple, timeout: int = 1800) -> None:
    deadline, results = time.time() + timeout, {}
    pending = {int(k): v for k, v in wanted.items()}
    while pending and time.time() < deadline:
        time.sleep(5)
        try:
            jobs = {j["id"]: j for j in _glue("GET", "jobs")["jobs"]}
        except Exception:  # noqa: BLE001
            continue
        for job_id, name in list(pending.items()):
            job = jobs.get(job_id)
            if not job or job["name"] != name:     # msp-glue restarted, or the job aged out of its list
                results[job_id] = {"name": name, "status": "unknown", "result": "no longer in msp-glue's job list"}
                pending.pop(job_id)
            elif job["status"] in ("done", "failed"):
                results[job_id] = job
                pending.pop(job_id)
    for job_id, name in pending.items():
        results[job_id] = {"name": name, "status": "unknown", "result": "still running after 30 minutes"}
    statuses = {r["status"] for r in results.values()}
    outcome = "failed" if "failed" in statuses else "done" if statuses == {"done"} else "unknown"
    store.update(entry, outcome, "; ".join(f"{r['name']}: {r['status']} ({r.get('result') or ''})"
                                           for r in results.values()))
    data.cache.clear(*clear)


# =============================================================================
# Configuration check (Golden Config backup, intended and compliance for one customer)
# =============================================================================
def config_check(user: User, code: str, address: str = "") -> Result:
    if not user.can_act:
        raise Forbidden("Starting a configuration check needs the engineer role.")
    tenant = _tenant(code)
    nb = clients.nautobot()
    group = nb.first("extras/dynamic-groups/", name=f"Customer {code} devices")
    members = nb.request("GET", f"extras/dynamic-groups/{group['id']}/members/", params={"limit": 1}) if group else None
    if not members or not members.get("count"):
        raise ActionError(f"{tenant.name} has no active network device with a primary IP address and a platform "
                          "Golden Config supports, so there is nothing to check.")
    job = nb.first("extras/jobs/", module_name="nautobot_golden_config.jobs", job_class_name="AllDevicesGoldenConfig")
    if not job:
        raise ActionError("The Golden Config job is not installed in Nautobot.")
    if _duplicate(user, "configuration check", tenant.name, seconds=600) or any(
            e["customer"] == code for e in store.unfinished("configuration check", 1800)):
        raise ActionError("A configuration check of this customer is already running.")
    entry = store.record(user.as_dict(), "configuration check", customer=code, target=tenant.name,
                         outcome="started", address=address)
    try:
        started = nb.request("POST", f"extras/jobs/{job['id']}/run/", {"data": {
            "tenant": [tenant.id], "fail_job_on_task_failure": True,
            "commit_message": f"Configuration check from the MSP Console by {user.email}"}})
    except APIError as exc:
        store.update(entry, "failed", str(exc))
        raise ActionError(f"Nautobot did not start the job: {exc}") from exc
    result_id = (started.get("job_result") or {}).get("id")
    link = f"{settings.public_url('nautobot')}/extras/job-results/{result_id}/" if result_id else ""
    store.update(entry, "started", detail={"job_result": result_id, "link": link})
    _background(f"follow configuration check {code}", _follow_config_check, entry, result_id, code)
    return Result(f"Golden Config is backing up and checking the network devices of {tenant.name}. Drift tickets "
                  "are updated when it finishes, usually within a few minutes.", entry)


def _job_status(result: dict) -> str:
    status = result.get("status")
    return str((status.get("value") if isinstance(status, dict) else status) or "").upper()


def _follow_config_check(entry: int, result_id: str | None, code: str, timeout: int = 1800) -> None:
    status = "UNKNOWN"
    if result_id:
        nb, deadline = clients.nautobot(), time.time() + timeout
        while time.time() < deadline:
            time.sleep(10)
            try:
                status = _job_status(nb.request("GET", f"extras/job-results/{result_id}/"))
            except Exception:  # noqa: BLE001
                continue
            if status in ("SUCCESS", "FAILURE", "REVOKED"):
                break
    if status == "SUCCESS":
        note, outcome = "Golden Config backed up and checked every network device.", "done"
    elif status in ("FAILURE", "REVOKED"):
        note, outcome = ("Golden Config finished with errors: at least one device could not be reached, backed up "
                         "or checked. The job result in Nautobot names them."), "errors"
    else:
        note, outcome = f"Golden Config did not report a result (status {status or 'unknown'}).", "unknown"
    try:
        queued = _glue("POST", "run/compliance")
        note += f" Drift tickets update queued on msp-glue (job {queued['job']})."
    except Exception as exc:  # noqa: BLE001
        note += f" msp-glue did not accept the drift ticket update: {exc}"
    store.update(entry, outcome, note)
    data.cache.clear("compliance", "backups", "devices", "open_tickets")


# =============================================================================
# Playbooks (Semaphore), always for a GLPI ticket or change of the same customer
# =============================================================================
def change_only_templates() -> set[str]:
    return set((settings.mappings.get("console") or {}).get("change_only_templates") or [])


def parse_variables(text: str) -> dict[str, str]:
    """'name: value' lines, one per line. Ansible's own variables (ansible_*) are refused: they could point
    the run at another host or account."""
    out: dict[str, str] = {}
    for number, line in enumerate((text or "").splitlines(), 1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not _VAR_NAME.match(key):
            raise ActionError(f"Variables, line {number}: write one 'name: value' per line.")
        if key in RESERVED_VARIABLES or key.lower().startswith("ansible_"):
            raise ActionError(f"The variable {key} cannot be set here.")
        out[key] = value.strip()[:500]
    if len(out) > 20:
        raise ActionError("At most 20 variables.")
    return out


def _in_entity(g, entity_id: int, customer_entity: int) -> bool:
    """True when entity_id is the customer's entity or one below it."""
    for _ in range(12):
        if entity_id == customer_entity:
            return True
        if entity_id <= 0:
            return False
        entity_id = int(g.get("Entity", entity_id).get("entities_id") or 0)
    return False


def check_itil(item_type: str, item_id: int, tenant, template_name: str) -> dict:
    if item_type not in ("Ticket", "Change"):
        raise ActionError("Choose a GLPI ticket or change.")
    change_only = template_name in change_only_templates()
    if change_only and item_type != "Change":
        raise ActionError(f"'{template_name}' changes devices, so it needs an approved GLPI change "
                          "(status Accepted), not a ticket.")
    if not tenant.glpi_entity_id:
        raise ActionError("This customer has no GLPI entity yet. Re-sync the customer first.")
    with clients.glpi() as g:
        try:
            item = g.get(item_type, item_id)
        except APIError as exc:
            raise ActionError(f"GLPI has no {item_type.lower()} #{item_id}.") from exc
        if int(item.get("is_deleted") or 0):
            raise ActionError(f"{item_type} #{item_id} is in the GLPI trash.")
        if not _in_entity(g, int(item.get("entities_id") or 0), int(tenant.glpi_entity_id)):
            raise ActionError(f"{item_type} #{item_id} belongs to another customer.")
    status = int(item.get("status") or 0)
    if item_type == "Ticket" and status >= STATUS_SOLVED:
        raise ActionError(f"Ticket #{item_id} is solved or closed. Use an open ticket.")
    if item_type == "Change":
        if status in CHANGE_FINISHED:
            raise ActionError(f"Change #{item_id} is finished. Use an open change.")
        if change_only and status != CHANGE_ACCEPTED:
            raise ActionError(f"Change #{item_id} is not approved yet: its status must be Accepted.")
    return item


def run_playbook(user: User, code: str, template_id: str, devices: list[str], item_type: str, item_id: str,
                 reason: str, variables_text: str = "", address: str = "") -> Result:
    if not user.can_run:
        raise Forbidden("Starting playbooks needs the automation role.")
    tenant = _tenant(code)
    if not tenant.semaphore_project_id:
        raise ActionError("This customer has no Semaphore project yet. Re-sync the customer first.")
    project = int(tenant.semaphore_project_id)
    sem = clients.semaphore()
    template = next((t for t in sem.templates(project) if str(t["id"]) == str(template_id)), None)
    if not template:
        raise ActionError("Choose one of this customer's templates.")
    if not data.limit_allowed(template):
        raise ActionError(f"'{template['name']}' would run on every device of {tenant.name}: the template does not "
                          "let a run choose its devices. In Semaphore, edit the template and allow the limit to be "
                          "set per run (allow_override_limit), then start it here.")
    known = {d["name"] for d in data.devices() if d["customer"] == code}
    devices = sorted({d.strip() for d in devices if d.strip()})
    if not devices:
        raise ActionError("Choose at least one device.")
    unknown = [d for d in devices if d not in known]
    if unknown:
        raise ActionError(f"Not a device of {tenant.name}: {', '.join(unknown)}.")
    reason = " ".join((reason or "").split())
    if len(reason) < 10:
        raise ActionError("Write why you run it (at least 10 characters). It goes into the ticket.")
    if not str(item_id).strip().lstrip("#").isdigit():
        raise ActionError("Give the GLPI ticket or change number.")
    item_number = int(str(item_id).strip().lstrip("#"))
    variables = parse_variables(variables_text)
    check_itil(item_type, item_number, tenant, template["name"])

    target = f"{template['name']} on {', '.join(devices)}"
    reference = f"{item_type} #{item_number}"
    if _duplicate(user, "playbook", target, reference):
        raise ActionError("You started the same run a moment ago. Check the task list before starting it again.")
    extra = {f"glpi_{item_type.lower()}_id": item_number, "msp_console_user": user.email, **variables}
    entry = store.record(user.as_dict(), "playbook", customer=code, target=target, ticket=reference, reason=reason,
                         outcome="started", detail={"template": template["name"], "devices": devices,
                                                    "variables": variables}, address=address)
    try:
        task = sem.run_task(project, template["id"], extra_vars=extra, limit=",".join(devices),
                            message=f"MSP Console, {user.email}, {reference}: {reason}"[:250])
    except APIError as exc:
        store.update(entry, "failed", str(exc))
        raise ActionError(f"Semaphore did not start the task: {exc}") from exc
    link = f"{settings.public_url('semaphore')}/project/{project}/history?t={task['id']}"
    store.update(entry, "started", detail={"project_id": project, "task_id": task["id"], "itemtype": item_type,
                                           "item_id": item_number, "link": link})
    try:
        with clients.glpi() as g:
            g.add_followup(item_type, item_number, (
                f"{MARKER} {html.escape(user.name)} ({html.escape(user.email)}) started Semaphore task "
                f"#{task['id']}: template '{html.escape(template['name'])}' on {html.escape(', '.join(devices))}."
                f"<br>Reason: {html.escape(reason)}"
                + (f"<br>Variables: {html.escape(str(variables))}" if variables else "")))
    except Exception as exc:  # noqa: BLE001 - the task runs anyway; say so in the audit trail
        store.update(entry, "started", f"Could not add the start note to GLPI: {exc}")
    _background(f"playbook {task['id']}", _follow_playbook, entry, item_type, item_number, project, task["id"])
    data.cache.clear(f"automation:{code}", "last_runs")
    return Result(f"Semaphore task #{task['id']} started ({template['name']} on {', '.join(devices)}). The result "
                  f"is added to GLPI {reference.lower()} when it finishes.", entry, task["id"])


def _follow_playbook(entry: int, item_type: str, item_id: int, project: int, task_id: int) -> None:
    try:
        status = changes.report_task_result(item_type, item_id, project, task_id, marker=MARKER,
                                            timeout=PLAYBOOK_WAIT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        store.update(entry, "unknown", f"Could not read or post the result of task #{task_id}: {exc}")
        return
    outcome = {"success": "success", "error": "error", "stopped": "stopped"}.get(status, "unknown")
    store.update(entry, outcome, f"Semaphore task #{task_id} finished with status {status}; "
                                 f"the result is on {item_type} #{item_id}.")
    data.cache.clear("last_runs")
    data.cache.clear_prefix("automation:")


# =============================================================================
# Tickets and changes (GLPI)
# =============================================================================
RISK_IMPACT = {"Low": 2, "Medium": 3, "High": 4}
_ids: dict[str, int] = {}


def _glpi_id(g, kind: str) -> int:
    """Ids the console needs in GLPI, found once: the NOC group and the "MSP Console" request source."""
    if kind not in _ids:
        if kind == "noc":
            found = g.find("Group", name=settings.mappings["glpi"]["noc_group"], entities_id=0)
            _ids[kind] = int(found[0]["id"]) if found else 0
        elif kind == "request_type":
            _ids[kind] = g.ensure("RequestType", {"name": "MSP Console"},
                                  {"comment": "Tickets opened from the MSP Console"})
    return _ids[kind]


def _category_id(g, full_name: str) -> int:
    parent = 0
    for part in [p.strip() for p in full_name.split(">")]:
        found = g.find("ITILCategory", name=part, itilcategories_id=parent)
        if not found:
            return 0
        parent = int(found[0]["id"])
    return parent


def _paragraphs(text: str) -> str:
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", text.strip()) if p.strip())


def _known_devices(code: str, names: list[str], tenant) -> list[dict]:
    known = {d["name"]: d for d in data.devices() if d["customer"] == code}
    wanted = sorted({n.strip() for n in names if n and n.strip()})
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise ActionError(f"Not a device of {tenant.name}: {', '.join(unknown)}.")
    return [known[n] for n in wanted]


def _when(value: str | None, what: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActionError(f"Give the {what} as a date and time.") from exc
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=data.tz())).astimezone(data.tz())


def create_ticket(user: User, code: str, kind: str, title: str, description: str, priority: int,
                  devices: list[str], planned_start: str | None = None, planned_end: str | None = None,
                  risk: str = "Low", template: str | None = None, rollback: str = "", address: str = "") -> Result:
    if not user.can_act:
        raise Forbidden("Opening tickets and changes needs the engineer role.")
    tenant = _tenant(code)
    if not tenant.glpi_entity_id:
        raise ActionError("This customer has no GLPI entity yet. Re-sync the customer first.")
    title = " ".join((title or "").split())
    if len(title) < 5:
        raise ActionError("Give the title (at least 5 characters).")
    description = (description or "").strip()
    if len(description) < 10:
        raise ActionError("Describe it (at least 10 characters). The description goes into GLPI as written.")
    if kind not in ("Incident", "Request", "Change"):
        raise ActionError("Choose an incident, a request or a change.")
    try:
        priority = min(5, max(1, int(priority)))
    except (TypeError, ValueError):
        priority = 3
    chosen = _known_devices(code, devices or [], tenant)
    if kind == "Change":
        return _create_change(user, tenant, title, description, chosen, planned_start, planned_end, risk, template,
                              rollback, address)
    if _duplicate(user, "ticket", f"{kind}: {title}", ticket=None):
        raise ActionError("You opened the same ticket a moment ago. Check the ticket list before opening it again.")
    entry = store.record(user.as_dict(), "ticket", customer=code, target=f"{kind}: {title}", outcome="started",
                         address=address)
    content = _paragraphs(description)
    if chosen:
        content += f"<p>Devices: {html.escape(', '.join(d['name'] for d in chosen))}</p>"
    content += f"<p>Opened in the MSP Console by {html.escape(user.name)} ({html.escape(user.email)}).</p>"
    try:
        with clients.glpi() as g:
            fields = {"name": title[:250], "content": content, "entities_id": int(tenant.glpi_entity_id),
                      "type": 1 if kind == "Incident" else 2, "urgency": priority, "impact": priority,
                      "requesttypes_id": _glpi_id(g, "request_type")}
            if _glpi_id(g, "noc"):
                fields["_groups_id_assign"] = _glpi_id(g, "noc")
            ticket_id = g.add("Ticket", fields)
            for d in chosen:
                if d.get("asset_id") and d.get("itemtype"):
                    try:
                        g.link_item_to_ticket(ticket_id, d["itemtype"], int(d["asset_id"]))
                    except Exception as exc:  # noqa: BLE001 - the ticket stands; say so
                        log.warning("Could not link %s to ticket %s: %s", d["name"], ticket_id, exc)
            due = data.glpi_time(g.get("Ticket", ticket_id).get("time_to_resolve"))
    except APIError as exc:
        store.update(entry, "failed", str(exc))
        raise ActionError(f"GLPI did not create the ticket: {exc}") from exc
    store.update(entry, "done", f"GLPI ticket #{ticket_id} created.", detail={"ticket_id": ticket_id})
    store.set_ticket(entry, f"Ticket #{ticket_id}")
    data.cache.clear("open_tickets")
    when = f", due {due.strftime('%d %b %H:%M')} under the {tenant.tier} service level" if due else ""
    return Result(f"GLPI ticket #{ticket_id} created for {tenant.name}{when}.", entry, ticket_id)


def _create_change(user: User, tenant, title: str, description: str, chosen: list[dict], planned_start, planned_end,
                   risk: str, template: str | None, rollback: str, address: str) -> Result:
    start, end = _when(planned_start, "planned start"), _when(planned_end, "planned end")
    if end <= start:
        raise ActionError("The planned end must be after the start.")
    if end < data.now():
        raise ActionError("The planned window is over already. Give a window that is still to come.")
    if len((rollback or "").strip()) < 10:
        raise ActionError("Write the back-out plan (at least 10 characters).")
    risk = risk if risk in RISK_IMPACT else "Low"
    by_template = {name: category for category, name in
                   ((settings.mappings.get("automation") or {}).get("change_templates") or {}).items()}
    category = settings.mappings["glpi"]["categories"].get("change", "")
    if template:
        if template not in by_template:
            raise ActionError(f"No GLPI change category runs '{template}'. Choose one of: "
                              f"{', '.join(sorted(by_template)) or 'none set up'}.")
        if not chosen:
            raise ActionError(f"Choose the devices '{template}' should run on.")
        category = by_template[template]
    if _duplicate(user, "change", title, ticket=None):
        raise ActionError("You raised the same change a moment ago. Check the change list before raising it again.")
    entry = store.record(user.as_dict(), "change", customer=tenant.code, target=title, outcome="started",
                         reason=f"{start:%d %b %H:%M} to {end:%d %b %H:%M}, risk {risk.lower()}", address=address)
    content = _paragraphs(description)
    if template:
        # msp-glue runs the template when the change is accepted, on these devices (see changes.py)
        content += f"<p>automation:<br>device: {html.escape(','.join(d['name'] for d in chosen))}</p>"
    elif chosen:
        content += f"<p>Devices: {html.escape(', '.join(d['name'] for d in chosen))}</p>"
    content += f"<p>Requested in the MSP Console by {html.escape(user.name)} ({html.escape(user.email)}).</p>"
    try:
        with clients.glpi() as g:
            fields = {"name": title[:250], "content": content, "entities_id": int(tenant.glpi_entity_id),
                      "urgency": 3, "impact": RISK_IMPACT[risk], "backoutplancontent": _paragraphs(rollback)}
            category_id = _category_id(g, category) if category else 0
            if category_id:
                fields["itilcategories_id"] = category_id
            if _glpi_id(g, "noc"):
                fields["_groups_id_assign"] = _glpi_id(g, "noc")
            change_id = g.add("Change", fields)
            task = {"changes_id": change_id, "state": 1, "content": "<p>Change window, planned in the MSP Console.</p>",
                    "plan": {"begin": data.to_glpi(start), "end": data.to_glpi(end)}}
            if _glpi_id(g, "noc"):
                task["groups_id_tech"] = _glpi_id(g, "noc")
            g.add("ChangeTask", task)
            for d in chosen:
                if d.get("asset_id") and d.get("itemtype"):
                    try:
                        g.add("Change_Item", {"changes_id": change_id, "itemtype": d["itemtype"],
                                              "items_id": int(d["asset_id"])})
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Could not link %s to change %s: %s", d["name"], change_id, exc)
    except APIError as exc:
        store.update(entry, "failed", str(exc))
        raise ActionError(f"GLPI did not create the change: {exc}") from exc
    store.update(entry, "done", f"GLPI change #{change_id} created.", detail={"change_id": change_id})
    store.set_ticket(entry, f"Change #{change_id}")
    data.cache.clear("changes")
    after = (f" When it is accepted, msp-glue runs '{template}' on {', '.join(d['name'] for d in chosen)}."
             if template else " It goes through approval in GLPI.")
    return Result(f"GLPI change #{change_id} created for {tenant.name}.{after}", entry, change_id)


def post_note(user: User, item_type: str, item_id: int, text: str, address: str = "") -> Result:
    """A private follow-up on a ticket or change, written with the Investigate assistant and checked by the
    engineer before posting."""
    if not user.can_act:
        raise Forbidden("Adding notes to tickets needs the engineer role.")
    if item_type not in ("Ticket", "Change"):
        raise ActionError("Choose a GLPI ticket or change.")
    text = (text or "").strip()
    if len(text) < 10:
        raise ActionError("Write the note first (at least 10 characters).")
    try:
        item_id = int(item_id)
    except (TypeError, ValueError) as exc:
        raise ActionError("Give the ticket or change number.") from exc
    entities = {int(t.glpi_entity_id): t for t in data.customers() if t.glpi_entity_id}
    with clients.glpi() as g:
        try:
            item = g.get(item_type, item_id)
        except APIError as exc:
            raise ActionError(f"GLPI has no {item_type.lower()} #{item_id}.") from exc
        if int(item.get("is_deleted") or 0):
            raise ActionError(f"{item_type} #{item_id} is in the GLPI trash.")
        tenant = next((t for e, t in entities.items() if _in_entity(g, int(item.get("entities_id") or 0), e)), None)
        if not tenant:
            raise ActionError(f"{item_type} #{item_id} does not belong to a managed customer.")
        entry = store.record(user.as_dict(), "investigation note", customer=tenant.code,
                             target=html.unescape(item.get("name") or ""), ticket=f"{item_type} #{item_id}",
                             reason="Draft cause checked and posted", outcome="started", address=address)
        body = (f"{MARKER} Note from {html.escape(user.name)} ({html.escape(user.email)}), written with the console's "
                f"assistant and checked before posting:<br>{html.escape(text[:4000]).replace(chr(10), '<br>')}")
        try:
            g.add_followup(item_type, item_id, body, is_private=True)
        except APIError as exc:
            store.update(entry, "failed", str(exc))
            raise ActionError(f"GLPI did not take the note: {exc}") from exc
    store.update(entry, "done", f"Private follow-up added to {item_type} #{item_id}.")
    return Result(f"Private follow-up added to GLPI {item_type.lower()} #{item_id}, marked as written with the "
                  "assistant.", entry)


# =============================================================================
# Maintenance (Zabbix)
# =============================================================================
def create_maintenance(user: User, code: str, devices: list[str] | None, name: str, start: str, minutes: int,
                       collect_data: bool, change_id: int | None, address: str = "") -> Result:
    if not user.can_act:
        raise Forbidden("Planning maintenance needs the engineer role.")
    tenant = _tenant(code)
    name = " ".join((name or "").split())
    if len(name) < 5:
        raise ActionError("Name the maintenance (at least 5 characters). Zabbix shows the name on every hidden problem.")
    begin = _when(start, "start")
    if begin < data.now() - timedelta(minutes=10):
        raise ActionError("The start is in the past. Maintenance starts now at the earliest.")
    try:
        minutes = int(minutes)
    except (TypeError, ValueError) as exc:
        raise ActionError("Give the length in minutes.") from exc
    if not 15 <= minutes <= 24 * 60:
        raise ActionError("A maintenance window lasts 15 minutes to 24 hours.")
    if devices is not None and not devices:
        raise ActionError("Choose the devices, or the whole customer.")
    if tenant.regulated and not change_id:
        raise ActionError(f"{tenant.name} is a regulated customer: maintenance needs a GLPI change. Choose the change "
                          "it belongs to.")
    if change_id:
        check_itil("Change", int(change_id), tenant, "")   # the customer's own, open change
    hosts = data.hosts()
    params: dict = {}
    if devices is None:
        if not tenant.zabbix_hostgroup_id:
            raise ActionError("This customer has no Zabbix host group yet. Re-sync the customer first.")
        params["groups"] = [{"groupid": str(tenant.zabbix_hostgroup_id)}]
        covered = f"every device of {tenant.name}"
    else:
        chosen = _known_devices(code, devices, tenant)
        missing = [d["name"] for d in chosen if d["id"] not in hosts]
        if missing:
            raise ActionError(f"Not monitored in Zabbix, so there is nothing to put in maintenance: {', '.join(missing)}.")
        params["hosts"] = [{"hostid": hosts[d["id"]]["hostid"]} for d in chosen]
        covered = f"{len(chosen)} device{'s' if len(chosen) != 1 else ''}"
    end = begin + timedelta(minutes=minutes)
    z = clients.zabbix()
    title = f"{name} ({tenant.code})"[:128]
    if z.call("maintenance.get", {"output": ["maintenanceid"], "filter": {"name": [title]}}):
        title = f"{name} ({tenant.code}, {begin:%d %b %H:%M})"[:128]
    description = (f"Planned in the MSP Console by {user.name} ({user.email})"
                   + (f" for change #{int(change_id)}" if change_id else "") + ".")
    entry = store.record(user.as_dict(), "maintenance", customer=code,
                         target=f"{'all devices' if devices is None else ', '.join(sorted(devices))}, {minutes} min, "
                                f"data collection {'on' if collect_data else 'off'}",
                         ticket=f"Change #{int(change_id)}" if change_id else "", reason=name, outcome="started",
                         address=address)
    try:
        created = z.call("maintenance.create", {
            "name": title, "active_since": int(begin.timestamp()), "active_till": int(end.timestamp()),
            "maintenance_type": 0 if collect_data else 1, "description": description,
            "timeperiods": [{"timeperiod_type": 0, "start_date": int(begin.timestamp()), "period": minutes * 60}],
            **params})
    except APIError as exc:
        store.update(entry, "failed", str(exc))
        raise ActionError(f"Zabbix did not create the maintenance: {exc}") from exc
    maintenance_id = created["maintenanceids"][0]
    store.update(entry, "done", f"Zabbix maintenance '{title}' created.", detail={"maintenance_id": maintenance_id})
    data.cache.clear("maintenances", "hosts", "changes")
    if change_id:
        try:
            with clients.glpi() as g:
                g.add_followup("Change", int(change_id), (
                    f"{MARKER} {html.escape(user.name)} planned Zabbix maintenance '{html.escape(title)}' for this change: "
                    f"{begin:%d %b %Y %H:%M} to {end:%H:%M}, {html.escape(covered)}."))
        except Exception as exc:  # noqa: BLE001 - the maintenance stands; say so
            store.update(entry, "done", f"Zabbix maintenance '{title}' created; the note on change #{change_id} "
                                        f"could not be added: {exc}")
    zone = data.now().strftime("%Z")
    return Result(f"Zabbix maintenance '{title}' set for {begin:%d %b %H:%M} to {end:%H:%M} {zone} on {covered}.",
                  entry, maintenance_id)


# =============================================================================
# Devices (Nautobot)
# =============================================================================
ROLE_NAMES = {"router": "router", "switch": "switch", "firewall": "firewall", "server": "server",
              "wireless": "access-point"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")


def add_device(user: User, code: str, site_id: str, name: str, role: str, platform: str, model: str, status: str,
               ip: str, serial: str, monitoring: bool, address: str = "") -> Result:
    if not user.can_act:
        raise Forbidden("Adding devices needs the engineer role.")
    tenant = _tenant(code)
    name = (name or "").strip().lower()
    if not _NAME.match(name):
        raise ActionError("Device names use lower-case letters, digits, dots and dashes, for example jkt01-sw03.")
    if any(d["name"] == name for d in data.devices()):
        raise ActionError(f"A device called {name} exists already.")
    site = next((s for s in data.sites(code) if s["id"] == site_id or s["name"] == site_id), None)
    if not site:
        raise ActionError("Choose one of the customer's sites.")
    if status not in ("Active", "Planned"):
        raise ActionError("A new device is Active or Planned.")
    ip = (ip or "").strip()
    if ip:
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError as exc:
            raise ActionError("Write the address as four numbers, for example 10.23.0.12.") from exc
        ip = str(parsed)
        if any(d["ip"] == ip for d in data.devices()):
            raise ActionError(f"{ip} is the address of another device already.")
    elif status == "Active":
        raise ActionError("An active device needs its management address, so Zabbix and Golden Config can reach it.")
    nb = clients.nautobot()
    prefix = None
    if ip:
        # Nautobot keeps an address only inside a prefix it knows; the address takes that prefix's length.
        prefixes = nb.list("ipam/prefixes/", contains=ip)
        prefix = max(prefixes, key=lambda p: int(p.get("prefix_length") or str(p.get("prefix", "/0")).split("/")[1]),
                     default=None)
        if not prefix:
            raise ActionError(f"Nautobot has no prefix that contains {ip}. Add the management network under IPAM > "
                              "Prefixes first (for example 10.23.0.0/24), then add the device.")
    role_name = ROLE_NAMES.get(role, role)
    found_role = nb.first("extras/roles/", name=role_name)
    found_platform = nb.first("dcim/platforms/", name=platform) if platform else None
    if platform and not found_platform:
        raise ActionError(f"Nautobot has no platform called {platform}.")
    device_type = nb.first("dcim/device-types/", model=(model or "").strip()) if model else None
    if not device_type:
        known = sorted(t["model"] for t in nb.list("dcim/device-types/"))[:12]
        raise ActionError(f"Nautobot has no device type '{model}'. Known types: {', '.join(known)}. Add the type in "
                          "Nautobot first, then the device.")
    if not found_role:
        raise ActionError(f"Nautobot has no device role called {role_name}.")
    entry = store.record(user.as_dict(), "add device", customer=code, target=f"{name} ({status}) at {site['name']}",
                         outcome="started", address=address)
    payload = {"name": name, "tenant": tenant.id, "location": site["id"], "role": found_role["id"],
               "device_type": device_type["id"], "status": nb.status_id(status), "serial": (serial or "").strip(),
               "custom_fields": {"msp_monitoring": bool(monitoring)}}
    if found_platform:
        payload["platform"] = found_platform["id"]
    try:
        device = nb.create("dcim/devices/", payload)
    except APIError as exc:
        store.update(entry, "failed", str(exc))
        raise ActionError(f"Nautobot did not add the device: {exc}") from exc
    if ip:
        try:
            _primary_address(nb, device, tenant, ip, prefix)
        except APIError as exc:
            # Without its address the device would sit half-made; take it out again and say why.
            try:
                nb.request("DELETE", f"dcim/devices/{device['id']}/")
            except Exception:  # noqa: BLE001
                pass
            store.update(entry, "failed", f"Address {ip} refused: {exc}")
            raise ActionError(f"Nautobot did not take the address {ip}, so the device was not added: {exc}") from exc
    store.update(entry, "done", "Added to Nautobot." + (" msp-glue adds it to Zabbix, GLPI and Golden Config."
                                                      if status == "Active" else ""), detail={"device_id": device["id"]})
    data.cache.clear("devices", f"sites:{code}")
    if status == "Active":
        message = f"{name} added to Nautobot. msp-glue adds it to Zabbix and GLPI within a minute."
    else:
        message = f"{name} added to Nautobot as Planned. Set it to Active with its address when it is installed."
    return Result(message, entry, device["id"])


def _primary_address(nb, device: dict, tenant, ip: str, prefix: dict) -> None:
    """mgmt0 with the address (the length of its prefix, in the prefix's namespace) as the primary address."""
    length = int(prefix.get("prefix_length") or str(prefix["prefix"]).split("/")[1])
    namespace = (prefix.get("namespace") or {}).get("id") if isinstance(prefix.get("namespace"), dict) else prefix.get("namespace")
    interface = nb.create("dcim/interfaces/", {"device": device["id"], "name": "mgmt0", "type": "virtual",
                                               "status": nb.status_id("Active"), "mgmt_only": True})
    address = nb.create("ipam/ip-addresses/", {"address": f"{ip}/{length}", "status": nb.status_id("Active"),
                                               "type": "host", "tenant": tenant.id,
                                               **({"namespace": namespace} if namespace else {})})
    nb.create("ipam/ip-address-to-interface/", {"ip_address": address["id"], "interface": interface["id"]})
    nb.patch("dcim/devices/", device["id"], {"primary_ip6" if ":" in ip else "primary_ip4": address["id"]})


# =============================================================================
# After a restart
# =============================================================================
def resume() -> None:
    """Pick up follow-ups that were running when the console stopped."""
    try:
        for e in store.unfinished("playbook", PLAYBOOK_WAIT_SECONDS):
            d = e["detail"]
            if d.get("task_id"):
                _background(f"playbook {d['task_id']}", _follow_playbook, e["id"], d["itemtype"], int(d["item_id"]),
                            int(d["project_id"]), int(d["task_id"]))
            else:
                store.update(e["id"], "unknown", "The console stopped before Semaphore answered.")
        for e in store.unfinished("configuration check", 3 * 3600):
            _background(f"follow configuration check {e['customer']}", _follow_config_check, e["id"],
                        e["detail"].get("job_result"), e["customer"])
        for e in store.unfinished("re-sync", 3 * 3600):
            _background(f"follow re-sync {e['customer']}", _follow_glue_jobs, e["id"], e["detail"].get("jobs") or {},
                        ("customers", "devices", "hosts"))
        for action in ("acknowledge", "problem note"):
            for e in store.unfinished(action, 3 * 3600):
                store.update(e["id"], "unknown", "The console stopped while Zabbix was answering.")
    except Exception:  # noqa: BLE001
        log.exception("Console: could not resume follow-ups")
