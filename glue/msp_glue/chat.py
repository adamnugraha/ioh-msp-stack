"""Chat alerts: Zabbix -> Versus Incident -> Telegram, Microsoft Teams or e-mail.

  mspctl chat setup     import the webhook "Versus chat", create its service user and the action
                        "MSP: chat alert" (problems on managed devices from mappings.yaml chat.min_severity)
  mspctl chat off       switch the action and the webhook off (nothing is deleted)
  mspctl chat test      send one test message through Versus to the configured channel

GLPI stays the record of every incident; chat only tells the NOC quickly. The first message waits
chat.delay (1 minute by default): a problem that clears by itself within it is never posted, and by
then the GLPI ticket number is known, so the message links to it. Recoveries, acknowledgements and
comments of a posted problem follow in the same chat.
"""

from __future__ import annotations

import logging
import re
import secrets as pysecrets
from pathlib import Path

from . import clients
from .http import APIError, make_session
from .settings import settings

log = logging.getLogger("msp_glue.chat")

MEDIA_NAME = "Versus chat"
ACTION_NAME = "MSP: chat alert"
SERVICE_USER = "svc-versus-chat"
VERSUS_URL = "http://versus-incident:3000/api/incidents"
SEED_FILE = Path("/app/seed/zabbix/media_versus_chat.yaml")


def config() -> dict:
    cfg = settings.mappings.get("chat") or {}
    severity = int(cfg.get("min_severity", 4))
    if not 0 <= severity <= 5:
        raise APIError(f"mappings.yaml chat.min_severity must be 0 to 5, not {severity}")
    delay = str(cfg.get("delay", "1m")).strip() or "0"
    match = re.fullmatch(r"(\d+)([smh]?)", delay)
    seconds = int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)] if match else -1
    # Zabbix steps last at least 60 seconds, so a delay is 0 (send at once) or one minute or more.
    if seconds != 0 and not 60 <= seconds <= 86400:
        raise APIError(f"mappings.yaml chat.delay must be 0 (at once) or between 1m and 24h, not '{delay}'")
    return {"min_severity": severity, "delay": delay if seconds else "0"}


def severity_mask(minimum: int) -> int:
    """Zabbix media severity bits (1 Not classified ... 32 Disaster) for this severity and worse."""
    return sum(1 << s for s in range(minimum, 6))


def _media(z) -> dict | None:
    found = z.call("mediatype.get", {"filter": {"name": [MEDIA_NAME]}, "output": ["mediatypeid", "status",
                                                                              "parameters"]})
    return found[0] if found else None


def setup(seed_file: Path = SEED_FILE) -> dict:
    cfg = config()
    z = clients.zabbix()
    zm = settings.mappings["zabbix"]
    if not seed_file.exists():
        raise APIError(f"{seed_file} is missing: msp-glue needs the kit's zabbix/media folder mounted at /app/seed/zabbix")
    z.call("configuration.import", {"format": "yaml", "source": seed_file.read_text(),
                                    "rules": {"mediaTypes": {"createMissing": True, "updateExisting": True}}})
    media = _media(z)
    if not media:
        raise APIError(f"Zabbix did not import the media type {MEDIA_NAME}")
    # The import resets the parameters to the file's values; fill in this platform's addresses.
    wanted = {"versus_url": VERSUS_URL, "glpi_public_url": settings.glpi_public_url or settings.public_url("glpi"),
              "console_url": settings.public_url("console")}
    params = media["parameters"]
    for p in params:
        if p["name"] in wanted:
            p["value"] = wanted[p["name"]]
    z.call("mediatype.update", {"mediatypeid": media["mediatypeid"], "status": 0, "parameters": params})

    # Service user that receives the alerts. In the NOC group too, so the name of the engineer who
    # acknowledges ({USER.FULLNAME}) shows in the message instead of "Inaccessible user".
    groups = []
    for name in (zm["service_usergroup"], zm["noc_usergroup"]):
        found = z.call("usergroup.get", {"filter": {"name": [name]}, "output": ["usrgrpid"]})
        if not found:
            raise APIError(f"Zabbix user group '{name}' is missing: run ./scripts/install.sh platform first")
        groups.append({"usrgrpid": found[0]["usrgrpid"]})
    role = z.call("role.get", {"filter": {"name": ["User role"]}, "output": ["roleid"]})[0]["roleid"]
    medias = [{"mediatypeid": media["mediatypeid"], "sendto": "noc-chat", "active": 0,
               "severity": severity_mask(cfg["min_severity"]), "period": "1-7,00:00-24:00"}]
    user = z.call("user.get", {"filter": {"username": [SERVICE_USER]}, "output": ["userid"]})
    if user:
        userid = user[0]["userid"]
        z.call("user.update", {"userid": userid, "medias": medias, "usrgrps": groups})
    else:
        userid = z.call("user.create", {"username": SERVICE_USER, "name": "NOC", "surname": "chat",
                                        "passwd": pysecrets.token_urlsafe(24) + "aA1!", "roleid": role,
                                        "usrgrps": groups, "medias": medias})["userids"][0]

    delayed = cfg["delay"] != "0"
    action = {
        "name": ACTION_NAME, "eventsource": 0, "status": 0,
        # With a delay, the message is step 2 of the escalation: sent after esc_period, and never sent
        # when the problem is resolved before that.
        "esc_period": cfg["delay"] if delayed else "1h",
        "filter": {"evaltype": 1, "conditions": [
            {"conditiontype": 26, "operator": 0, "value": "msp-glue", "value2": "managed_by"},
            {"conditiontype": 4, "operator": 5, "value": str(cfg["min_severity"])},
        ]},
        "operations": [{"operationtype": 0, "esc_step_from": 2 if delayed else 1, "esc_step_to": 2 if delayed else 1,
                        "opmessage": {"default_msg": 1, "mediatypeid": media["mediatypeid"]},
                        "opmessage_usr": [{"userid": userid}]}],
        "recovery_operations": [{"operationtype": 11, "opmessage": {"default_msg": 1}}],
        "update_operations": [{"operationtype": 12, "opmessage": {"default_msg": 1}}],
    }
    existing = z.call("action.get", {"filter": {"name": [ACTION_NAME]}, "output": ["actionid"]})
    if existing:
        action["actionid"] = existing[0]["actionid"]
        action.pop("eventsource")
        z.call("action.update", action)
    else:
        z.call("action.create", action)
    names = {2: "Warning", 3: "Average", 4: "High", 5: "Disaster", 0: "Not classified", 1: "Information"}
    log.info("Zabbix: chat alerts on (%s and worse, first message after %s)", names[cfg["min_severity"]],
             cfg["delay"])
    return {"media type": MEDIA_NAME, "action": ACTION_NAME, "user": SERVICE_USER,
            "severity": f"{names[cfg['min_severity']]} and worse",
            "first message": f"after {cfg['delay']}" if delayed else "at once"}


def off() -> dict:
    z = clients.zabbix()
    done = {}
    action = z.call("action.get", {"filter": {"name": [ACTION_NAME]}, "output": ["actionid"]})
    if action:
        z.call("action.update", {"actionid": action[0]["actionid"], "status": 1})
        done["action"] = "disabled"
    media = _media(z)
    if media:
        z.call("mediatype.update", {"mediatypeid": media["mediatypeid"], "status": 1})
        done["media type"] = "disabled"
    return done or {"chat": "was not set up"}


def test(url: str = VERSUS_URL) -> str:
    """One sample alert straight to Versus (not through Zabbix), to prove the channel works."""
    body = {
        "kind": "problem", "severity": "High", "nseverity": 4,
        "problem": "Test message from the IOH managed services platform",
        "customer": "Test customer", "customer_code": "", "device": "test-device-01", "site": "NOC",
        "address": "", "started": "now", "duration": "", "opdata": "",
        "event_id": "0", "event_url": settings.public_url("zabbix"), "ticket_id": "", "ticket_url": "",
        "console_url": settings.public_url("console"), "update_user": "", "update_action": "", "update_message": "",
    }
    response = make_session(retries=0).post(url, json=body, timeout=30)
    if response.status_code >= 300:
        raise APIError(f"Versus answered HTTP {response.status_code}: {response.text[:300]}")
    return response.text[:200]
