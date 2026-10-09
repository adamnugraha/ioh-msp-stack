"""Configuration drift (Golden Config compliance) -> one GLPI ticket per device.

Runs after the nightly Golden Config compliance job. A device with at least one
non-compliant feature gets one open ticket in its customer's entity; the ticket gets a
follow-up when the list of drifted features changes, and a solution once the device
is compliant again.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from . import clients
from .clients.glpi import STATUS_SOLVED, TYPE_REQUEST
from .settings import settings
from .tenants import managed_tenants

log = logging.getLogger("msp_glue.compliance")


def run() -> dict:
    nb = clients.nautobot()
    tenants = {t.id: t for t in managed_tenants(nb) if t.glpi_entity_id}
    rows = nb.list("plugins/golden-config/config-compliance/", depth=1)
    by_device: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_device[row["device"]["id"]].append(row)

    opened = updated = solved = 0
    with clients.glpi() as g:
        category = _category_id(g, settings.mappings["glpi"]["categories"]["compliance"])
        noc = g.find("Group", name=settings.mappings["glpi"]["noc_group"], entities_id=0)
        noc_group = int(noc[0]["id"]) if noc else None
        for device_id, results in by_device.items():
            device = nb.request("GET", f"dcim/devices/{device_id}/", params={"depth": 1})
            tenant_ref = device.get("tenant") or {}
            tenant = tenants.get(tenant_ref.get("id"))
            if not tenant:
                continue
            cf = device.get("custom_fields") or {}
            drift = sorted({(r.get("rule") or {}).get("display") or (r.get("rule") or {}).get("name", "?")
                            for r in results if not r.get("compliance")})
            ticket_id = cf.get("msp_drift_ticket_id")
            if drift:
                content = _content(device, results)
                if ticket_id and _is_open(g, ticket_id):
                    last = _last_followup(g, ticket_id)
                    if content not in (last or ""):
                        g.add_followup("Ticket", ticket_id, content)
                        updated += 1
                    continue
                fields = {
                    "name": f"[{tenant.name}] Configuration drift on {device['name']}",
                    "content": content, "entities_id": int(tenant.glpi_entity_id), "type": TYPE_REQUEST,
                    "urgency": 3 if tenant.regulated else 2, "itilcategories_id": category,
                }
                if noc_group:
                    fields["_groups_id_assign"] = noc_group
                ticket_id = g.add("Ticket", fields)
                # Record the ticket first, so a failure below can never cause a duplicate next run.
                nb.set_custom_fields("dcim/devices/", device_id, {"msp_drift_ticket_id": ticket_id})
                asset_id, itemtype = cf.get("msp_glpi_asset_id"), cf.get("msp_glpi_itemtype")
                if asset_id and itemtype:
                    try:
                        g.link_item_to_ticket(ticket_id, itemtype, int(asset_id))
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Could not link %s %s to ticket %s: %s", itemtype, asset_id, ticket_id, exc)
                opened += 1
            elif ticket_id:
                if _is_open(g, ticket_id):
                    g.add("ITILSolution", {"itemtype": "Ticket", "items_id": ticket_id,
                                           "content": "All Golden Config compliance rules pass again."})
                    solved += 1
                nb.set_custom_fields("dcim/devices/", device_id, {"msp_drift_ticket_id": None})
    summary = {"opened": opened, "updated": updated, "solved": solved}
    log.info("Compliance tickets: %s", summary)
    return summary


def _content(device: dict, results: list[dict]) -> str:
    lines = [f"<p>Golden Config found configuration drift on <b>{device['name']}</b>.</p><ul>"]
    for r in sorted(results, key=lambda x: (x.get("rule") or {}).get("display", "")):
        if r.get("compliance"):
            continue
        rule = (r.get("rule") or {}).get("display", "rule")
        missing = (r.get("missing") or "").strip()
        extra = (r.get("extra") or "").strip()
        lines.append(f"<li><b>{rule}</b>")
        if missing:
            lines.append(f"<br>Missing:<pre>{missing[:1500]}</pre>")
        if extra:
            lines.append(f"<br>Not expected:<pre>{extra[:1500]}</pre>")
        lines.append("</li>")
    lines.append("</ul><p>Fix the device or update the intended configuration in Git, then re-run compliance.</p>")
    return "".join(lines)


def _is_open(g, ticket_id) -> bool:
    try:
        return int(g.get("Ticket", int(ticket_id))["status"]) < STATUS_SOLVED
    except Exception:  # noqa: BLE001 - deleted ticket counts as closed
        return False


def _last_followup(g, ticket_id) -> str:
    items = g.request("GET", f"Ticket/{int(ticket_id)}/ITILFollowup") or []
    return items[-1]["content"] if items else g.get("Ticket", int(ticket_id)).get("content", "")


def _category_id(g, full_name: str) -> int:
    parent = 0
    for part in [p.strip() for p in full_name.split(">")]:
        found = g.find("ITILCategory", name=part, itilcategories_id=parent)
        if not found:
            return 0
        parent = int(found[0]["id"])
    return parent
