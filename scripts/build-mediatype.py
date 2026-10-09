#!/usr/bin/env python3
"""Build the importable Zabbix media type (YAML) from zabbix/media/glpi_msp.js.

Run after editing the JavaScript:  python3 scripts/build-mediatype.py
Then import zabbix/media/media_glpi_msp.yaml in Zabbix (Alerts > Media types > Import),
or let "mspctl bootstrap platform" import it.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "zabbix/media/glpi_msp.js").read_text(encoding="utf-8")


class Literal(str):
    pass


def literal_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(Literal, literal_representer)

PARAMETERS = {
    "alert_message": "{ALERT.MESSAGE}",
    "alert_subject": "{ALERT.SUBJECT}",
    "event_id": "{EVENT.ID}",
    "event_name": "{EVENT.NAME}",
    "event_nseverity": "{EVENT.NSEVERITY}",
    "event_source": "{EVENT.SOURCE}",
    "event_tags": "{EVENT.TAGSJSON}",
    "event_update_status": "{EVENT.UPDATE.STATUS}",
    "event_value": "{EVENT.VALUE}",
    "glpi_app_token": "",
    "glpi_default_entity_id": "0",
    "glpi_public_url": "",
    "glpi_requesttype_id": "",
    "glpi_ticket_id": "{EVENT.TAGS.__zbx_glpi_ticket_id}",
    "glpi_url": "<PLACE GLPI URL>",
    "glpi_user_token": "<PLACE GLPI USER TOKEN>",
    "host_name": "{HOST.NAME}",
    "trigger_id": "{TRIGGER.ID}",
    "zabbix_url": "{$ZABBIX.URL}",
}

PROBLEM_MSG = (
    "<p><b>Problem started:</b> {EVENT.DATE} {EVENT.TIME}<br>"
    "<b>Customer:</b> {EVENT.TAGS.tenant_name}<br>"
    "<b>Host:</b> {HOST.NAME} ({HOST.IP})<br>"
    "<b>Site:</b> {EVENT.TAGS.site}<br>"
    "<b>Problem:</b> {EVENT.NAME}<br>"
    "<b>Severity:</b> {EVENT.SEVERITY}<br>"
    "<b>Operational data:</b> {EVENT.OPDATA}<br>"
    "<b>Zabbix event:</b> {EVENT.ID}</p>"
)
RECOVERY_MSG = (
    "<p><b>Recovered in Zabbix</b> after {EVENT.DURATION}, at {EVENT.RECOVERY.DATE} {EVENT.RECOVERY.TIME}.<br>"
    "<b>Problem:</b> {EVENT.NAME}<br><b>Host:</b> {HOST.NAME}<br><b>Zabbix event:</b> {EVENT.ID}</p>"
)
UPDATE_MSG = (
    "<p>{USER.FULLNAME} {EVENT.UPDATE.ACTION} the problem at {EVENT.UPDATE.DATE} {EVENT.UPDATE.TIME}.</p>"
    "<p>{EVENT.UPDATE.MESSAGE}</p>"
    "<p>Current status: {EVENT.STATUS}, age {EVENT.AGE}, acknowledged: {EVENT.ACK.STATUS}.</p>"
)

export = {
    "zabbix_export": {
        "version": "7.0",
        "media_types": [{
            "name": "GLPI MSP",
            "type": "WEBHOOK",
            "parameters": [{"name": k, "value": v} if v != "" else {"name": k} for k, v in sorted(PARAMETERS.items())],
            "status": "DISABLED",
            # Three attempts are safe: a retried "problem" call finds the ticket it already
            # opened (by the [ZBX-<event id>] marker) instead of opening a second one.
            "attempts": "3",
            "attempt_interval": "30s",
            "timeout": "30s",
            "script": Literal(SCRIPT),
            "process_tags": "YES",
            "show_event_menu": "YES",
            "event_menu_url": "{EVENT.TAGS.__zbx_glpi_ticket_link}",
            "event_menu_name": "GLPI ticket {EVENT.TAGS.__zbx_glpi_ticket_id}",
            "description": Literal(
                "Opens GLPI incidents in the customer's entity, links the affected asset, adds follow-ups on\n"
                "updates and a solution on recovery. Requires the host tags written by msp-glue.\n"
                "Source: zabbix/media/glpi_msp.js in the platform kit.\n"
            ),
            "message_templates": [
                {"event_source": "TRIGGERS", "operation_mode": "PROBLEM",
                 "subject": "Problem: {EVENT.NAME}", "message": PROBLEM_MSG},
                {"event_source": "TRIGGERS", "operation_mode": "RECOVERY",
                 "subject": "Resolved in {EVENT.DURATION}: {EVENT.NAME}", "message": RECOVERY_MSG},
                {"event_source": "TRIGGERS", "operation_mode": "UPDATE",
                 "subject": "Updated problem: {EVENT.NAME}", "message": UPDATE_MSG},
            ],
        }],
    }
}

out = ROOT / "zabbix/media/media_glpi_msp.yaml"
out.write_text(yaml.dump(export, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")
print(f"Wrote {out.relative_to(ROOT)}")
