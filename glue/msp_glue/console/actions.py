"""Changes made from the console.

Every action checks the person's role, writes an audit entry before it starts, and then works
through msp-glue's job queue or the tool's own API. Actions that take a while (re-sync, configuration
check, playbook) are followed by a background thread that writes the outcome to the same audit entry;
playbook results also go to the GLPI ticket or change the run was made for.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time

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


def _duplicate(user: User, action: str, target: str, ticket: str = "", seconds: int = 60) -> bool:
    """The same person asked for the same thing a moment ago (a double click or a reloaded form)."""
    recent = store.entries(limit=20, email=user.email, action=action, since=time.time() - seconds)
    return any(e["target"] == target and e["ticket"] == ticket and e["outcome"] != "failed" for e in recent)


# =============================================================================
# Acknowledge a problem
# =============================================================================
def acknowledge(user: User, eventid: str, note: str, address: str = "") -> str:
    if not user.can_act:
        raise ActionError("Acknowledging problems needs the engineer role.")
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
    store.update(entry, "done")
    data.cache.clear("problems")
    done = "Note added." if already else "Problem acknowledged."
    return done + (f" Zabbix also adds it to GLPI ticket #{ticket}." if ticket else "")


# =============================================================================
# Re-sync a customer (tenant set-up, then its devices)
# =============================================================================
def resync(user: User, code: str, address: str = "") -> str:
    if not user.can_act:
        raise ActionError("Re-syncing customers needs the engineer role.")
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
    return (f"Re-sync of {tenant.name} queued on msp-glue (jobs {', '.join(str(j) for j in wanted)}). "
            "The result appears in the audit trail and under Integration jobs.")


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
def config_check(user: User, code: str, address: str = "") -> str:
    if not user.can_act:
        raise ActionError("Starting a configuration check needs the engineer role.")
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
    return (f"Golden Config is backing up and checking the network devices of {tenant.name}. Drift tickets are "
            "updated when it finishes, usually within a few minutes.")


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
                 reason: str, variables_text: str = "", address: str = "") -> str:
    if not user.can_run:
        raise ActionError("Starting playbooks needs the automation role.")
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
    return (f"Semaphore task #{task['id']} started ({template['name']} on {', '.join(devices)}). The result is "
            f"added to GLPI {reference.lower()} when it finishes.")


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
