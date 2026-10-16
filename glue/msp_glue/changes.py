"""Approved GLPI change -> Semaphore task -> result back on the change.

Flow:
  1. An engineer raises a Change in the customer's GLPI entity, picks a category that
     maps to a Semaphore template (mappings.yaml > automation.change_templates) and
     writes the variables in the description, for example:
         automation:
           device: jkt01-core-01
           interface: GigabitEthernet0/2
  2. The change is approved and its status is set to "Accepted".
  3. GLPI sends a webhook; msp-glue checks the status, finds the customer's Semaphore
     project and template, starts the task and adds a follow-up with the task number.
  4. When the task finishes, msp-glue adds a follow-up with the result and the log tail.
A change is only ever automated once (the follow-up marker is checked first).
"""

from __future__ import annotations

import html
import logging
import re
import threading

from . import clients
from .clients.glpi import CHANGE_ACCEPTED, VALIDATION_REFUSED
from .settings import settings
from .tenants import managed_tenants

log = logging.getLogger("msp_glue.changes")

MARKER = "[msp-glue automation]"
_VAR_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$")
_ANSI = re.compile(r"\x1b?\[[0-9;]*m")  # colour codes in Ansible output


def handle_change(change_id: int) -> str:
    with clients.glpi() as g:
        change = g.get("Change", change_id, expand_dropdowns="true")
        if int(_raw_status(g, change_id)) != CHANGE_ACCEPTED:
            return "ignored: change is not in status Accepted"
        if int(change.get("global_validation") or 0) == VALIDATION_REFUSED:
            return "ignored: validation refused"
        followups = g.request("GET", f"Change/{change_id}/ITILFollowup") or []
        if any(MARKER in (f.get("content") or "") for f in followups):
            return "ignored: already automated"

        category = html.unescape(str(change.get("itilcategories_id") or ""))
        template_name = settings.mappings["automation"]["change_templates"].get(category)
        if not template_name:
            return f"ignored: category '{category}' has no automation"

        entity_id = _raw_field(g, change_id, "entities_id")
        tenant = next((t for t in managed_tenants() if str(t.glpi_entity_id) == str(entity_id)), None)
        if not tenant or not tenant.semaphore_project_id:
            g.add_followup("Change", change_id, f"{MARKER} No Semaphore project for entity {entity_id}; not started.")
            return "error: no tenant"

        variables = parse_variables(change.get("content") or "")
        # Ansible's own variables (ansible_host, ansible_user, ...) could point the run at another
        # host or account, so they are never taken from a change description.
        refused = sorted(k for k in variables if k.lower().startswith("ansible_") or k == "glpi_change_id")
        for key in refused:
            variables.pop(key)
        sem = clients.semaphore()
        project_id = int(tenant.semaphore_project_id)
        template = next((t for t in sem.templates(project_id) if t["name"] == template_name), None)
        if not template:
            g.add_followup("Change", change_id, f"{MARKER} Template '{template_name}' not found in project {tenant.code}.")
            return "error: template missing"

        device = variables.get("device", "")
        if device and not (template.get("task_params") or {}).get("allow_override_limit"):
            # Semaphore would silently ignore the limit and run on the customer's whole inventory.
            g.add_followup("Change", change_id, f"{MARKER} Not started: template '{template_name}' does not allow a "
                           f"device limit, so it would run on every device of {tenant.code}. Allow the limit to be "
                           "set per run in the template (allow_override_limit), then start the run for this change "
                           "from the MSP Console.")
            return "error: template ignores the device limit"
        extra = {"glpi_change_id": change_id, **variables}
        task = sem.run_task(project_id, template["id"], extra_vars=extra, limit=device,
                            message=f"GLPI change #{change_id}: {change.get('name', '')}"[:250])
        g.add_followup("Change", change_id,
                       f"{MARKER} Started Semaphore task #{task['id']} (template '{template_name}', "
                       f"project {tenant.code}) with variables {extra}."
                       + (f" Ignored (not allowed in a change): {', '.join(refused)}." if refused else ""))
    threading.Thread(target=_report_result, args=(change_id, project_id, task["id"]), daemon=True).start()
    return f"started task {task['id']}"


def _raw_status(g, change_id: int) -> int:
    return int(g.get("Change", change_id)["status"])


def _raw_field(g, change_id: int, name: str):
    return g.get("Change", change_id).get(name)


def parse_variables(content: str) -> dict:
    """Read 'key: value' lines that follow a line 'automation:' in the change description."""
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", html.unescape(content), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    variables: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if re.match(r"^\s*automation\s*:\s*$", line, flags=re.I):
            inside = True
            continue
        if inside:
            match = _VAR_LINE.match(line)
            if match:
                variables[match.group(1)] = match.group(2)
            elif line.strip():
                break
    return variables


def _report_result(change_id: int, project_id: int, task_id: int) -> None:
    report_task_result("Change", change_id, project_id, task_id)


def report_task_result(itemtype: str, item_id: int, project_id: int, task_id: int, marker: str = MARKER,
                       timeout: int = 4 * 3600) -> str:
    """Wait for a Semaphore task (up to four hours: patch runs take a while), then add its status and the
    last 40 log lines to a GLPI ticket or change."""
    sem = clients.semaphore()
    status = "unknown"
    try:
        task = sem.wait(project_id, task_id, timeout=timeout, poll=10)
        status = str(task.get("status"))
        output = _ANSI.sub("", sem.final_output(project_id, task_id))
        tail = html.escape("\n".join(output.splitlines()[-40:]))
        body = (f"{marker} Semaphore task #{task_id} finished with status <b>{html.escape(status)}</b>."
                f"<pre>{tail}</pre>")
    except Exception as exc:  # noqa: BLE001
        body = f"{marker} Could not read the result of Semaphore task #{task_id}: {html.escape(str(exc))}"
    with clients.glpi() as g:
        g.add_followup(itemtype, item_id, body)
    return status
