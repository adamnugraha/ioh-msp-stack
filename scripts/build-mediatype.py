#!/usr/bin/env python3
"""Build the importable Zabbix media types (YAML) from their JavaScript sources:

  zabbix/media/glpi_msp.js     -> media_glpi_msp.yaml     (webhook "GLPI MSP", imported by "mspctl bootstrap platform")
  zabbix/media/versus_chat.js  -> media_versus_chat.yaml  (webhook "Versus chat", imported by "mspctl chat setup")

Run after editing either script:  python3 scripts/build-mediatype.py
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

# ----------------------------------------------------------------------------- Versus chat
CHAT_PARAMETERS = {
    "console_url": "",            # set by "mspctl chat setup" (https://console.<MSP_DOMAIN>)
    "event_date": "{EVENT.DATE}",
    "event_duration": "{EVENT.DURATION}",
    "event_id": "{EVENT.ID}",
    "event_name": "{EVENT.NAME}",
    "event_nseverity": "{EVENT.NSEVERITY}",
    "event_opdata": "{EVENT.OPDATA}",
    "event_severity": "{EVENT.SEVERITY}",
    "event_source": "{EVENT.SOURCE}",
    "event_tags": "{EVENT.TAGSJSON}",
    "event_time": "{EVENT.TIME}",
    "event_update_status": "{EVENT.UPDATE.STATUS}",
    "event_value": "{EVENT.VALUE}",
    "glpi_public_url": "",        # set by "mspctl chat setup"
    "glpi_ticket_id": "{EVENT.TAGS.__zbx_glpi_ticket_id}",
    "host_ip": "{HOST.IP}",
    "host_name": "{HOST.NAME}",
    "http_proxy": "",
    "trigger_id": "{TRIGGER.ID}",
    "update_action": "{EVENT.UPDATE.ACTION}",
    "update_message": "{EVENT.UPDATE.MESSAGE}",
    "update_user": "{USER.FULLNAME}",
    "versus_url": "http://versus-incident:3000/api/incidents",
    "zabbix_url": "{$ZABBIX.URL}",
}
chat = {
    "zabbix_export": {
        "version": "7.0",
        "media_types": [{
            "name": "Versus chat",
            "type": "WEBHOOK",
            "parameters": [{"name": k, "value": v} if v != "" else {"name": k} for k, v in sorted(CHAT_PARAMETERS.items())],
            "status": "DISABLED",
            "attempts": "3",
            "attempt_interval": "30s",
            "timeout": "15s",
            "script": Literal((ROOT / "zabbix/media/versus_chat.js").read_text(encoding="utf-8")),
            "description": Literal(
                "Posts problems, updates and recoveries on managed devices to Versus Incident, which sends them to\n"
                "the NOC's Telegram group, Microsoft Teams channel or mailbox. Set up by ./scripts/install.sh chat.\n"
                "Source: zabbix/media/versus_chat.js in the platform kit.\n"
            ),
            # The script builds its own message; these texts only show in Zabbix's own lists.
            "message_templates": [
                {"event_source": "TRIGGERS", "operation_mode": "PROBLEM", "subject": "Problem: {EVENT.NAME}",
                 "message": "{EVENT.SEVERITY} problem on {HOST.NAME} since {EVENT.DATE} {EVENT.TIME}: {EVENT.NAME}"},
                {"event_source": "TRIGGERS", "operation_mode": "RECOVERY", "subject": "Resolved: {EVENT.NAME}",
                 "message": "Resolved after {EVENT.DURATION}: {EVENT.NAME} on {HOST.NAME}"},
                {"event_source": "TRIGGERS", "operation_mode": "UPDATE", "subject": "Updated: {EVENT.NAME}",
                 "message": "{USER.FULLNAME} {EVENT.UPDATE.ACTION} the problem: {EVENT.UPDATE.MESSAGE}"},
            ],
        }],
    }
}
out = ROOT / "zabbix/media/media_versus_chat.yaml"
out.write_text(yaml.dump(chat, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")
print(f"Wrote {out.relative_to(ROOT)}")
