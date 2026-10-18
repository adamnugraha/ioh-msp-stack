"""Investigate a problem: the console gathers evidence from every tool (read-only, with its own accounts),
a language model drafts the likely cause and next steps, and the engineer checks the draft before any of
it reaches a ticket.

Off until a model endpoint is set:
  CONSOLE_AI_URL      an OpenAI-compatible base URL, for example http://ollama:11434/v1 or a Netra endpoint
  CONSOLE_AI_MODEL    the model name the endpoint expects
  CONSOLE_AI_KEY      the API key, when the endpoint needs one (kept in .env, never shown)

Nothing is sent anywhere else, and configuration lines are masked before they leave the console.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta

from .. import clients
from ..http import make_session
from ..settings import settings
from . import actions, data, detail, store
from .auth import User

log = logging.getLogger("msp_glue.console.investigate")

KEEP_SECONDS = 3600
_lock = threading.Lock()
_runs: dict[str, dict] = {}

SYSTEM = (
    "You help network operations engineers of a managed service provider find the cause of a monitoring problem. "
    "You receive evidence gathered from the provider's tools. Use only that evidence; when it does not show the "
    "cause, say so and set confidence to low. Answer with one JSON object and nothing else, with the keys: "
    "cause (two to four plain sentences), confidence (low, medium or high), next_steps (a list of at most four "
    "objects with a text key, and a playbook key only when one of the read-only playbooks listed in the evidence "
    "would help: {\"template\": name, \"devices\": [device names]}), note (a short note for the ticket, in plain "
    "text: what was seen, the likely cause, what to do next). Never invent device names, addresses or values."
)


def endpoint() -> tuple[str, str, str]:
    return (os.environ.get("CONSOLE_AI_URL", "").strip().rstrip("/"), os.environ.get("CONSOLE_AI_MODEL", "").strip(),
            os.environ.get("CONSOLE_AI_KEY", "").strip())


def enabled() -> bool:
    url, model, _key = endpoint()
    return bool(url and model)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _public(run: dict) -> dict:
    """The run as the front end reads it (types.ts Investigation)."""
    url, model, _key = endpoint()
    return {
        "id": run["id"], "eventid": run["eventid"], "started": _iso(run["started"]), "status": run["status"],
        "model": {"name": model or "not set", "endpoint": re.sub(r"//[^/@]+@", "//", url) or "not set"},
        "steps": [{k: s[k] for k in ("id", "tool", "title", "status", "finding", "link")} for s in run["steps"]],
        "cause": run.get("cause"), "confidence": run.get("confidence"), "nextSteps": run.get("next_steps") or [],
        "draftNote": run.get("note"), "sample": False, "error": run.get("error"),
    }


def _prune() -> None:
    cutoff = time.time() - KEEP_SECONDS
    for key in [k for k, r in _runs.items() if r["at"] < cutoff]:
        _runs.pop(key, None)


def start(user: User, eventid: str, address: str = "") -> dict:
    if not user.can_act:
        raise actions.Forbidden("Investigating problems needs the engineer role.")
    if not enabled():
        raise actions.ActionError("Investigate is switched off: no model endpoint is set. An administrator sets "
                                  "CONSOLE_AI_URL and CONSOLE_AI_MODEL in .env (see the guide) and restarts the console.",
                                  status=409)
    problem = next((p for p in data.problems() if p["eventid"] == str(eventid)), None)
    if not problem:
        data.cache.clear("problems")
        problem = next((p for p in data.problems() if p["eventid"] == str(eventid)), None)
    if not problem:
        raise actions.ActionError("This problem is resolved already, or it is not on a device the platform manages.")
    run_id = f"inv-{eventid}-{secrets.token_hex(4)}"
    steps = [
        {"id": "zabbix", "tool": "Zabbix", "title": "Problem, recent events and latest values", "status": "waiting",
         "finding": "", "link": problem["zabbix_link"]},
        {"id": "nautobot", "tool": "Nautobot", "title": "Device record, interfaces and neighbours", "status": "waiting",
         "finding": "", "link": None},
        {"id": "glpi", "tool": "GLPI", "title": "The ticket and its follow-ups", "status": "waiting", "finding": "",
         "link": problem.get("ticket_link")},
        {"id": "golden", "tool": "Nautobot", "title": "Golden Config backup and compliance", "status": "waiting",
         "finding": "", "link": None},
        {"id": "semaphore", "tool": "Semaphore", "title": "Recent playbook runs on the device", "status": "waiting",
         "finding": "", "link": None},
    ]
    run = {"id": run_id, "eventid": str(eventid), "started": data.now(), "status": "running", "steps": steps,
           "at": time.time(), "user": user.as_dict(), "problem": problem}
    entry = store.record(user.as_dict(), "investigate", customer=problem["customer"],
                         target=f"{problem['device']}: {problem['name']}",
                         ticket=f"Ticket #{problem['ticket_id']}" if problem.get("ticket_id") else "",
                         outcome="started", detail={"eventid": str(eventid), "run": run_id}, address=address)
    run["entry"] = entry
    with _lock:
        _prune()
        _runs[run_id] = run
    threading.Thread(target=_run, args=(run,), daemon=True, name=f"investigate {eventid}").start()
    return _public(run)


def get(run_id: str) -> dict:
    with _lock:
        run = _runs.get(run_id)
    if not run:
        raise actions.ActionError("That investigation is no longer kept. Start a new one.", status=404)
    return _public(run)


# ============================================================================== evidence
def _step(run: dict, step_id: str, func) -> object:
    step = next(s for s in run["steps"] if s["id"] == step_id)
    step["status"] = "running"
    try:
        finding, evidence, link = func()
        step.update(status="done", finding=finding)
        if link:
            step["link"] = link
        return evidence
    except Exception as exc:  # noqa: BLE001 - one tool down: say so and go on with the others
        log.warning("Investigate %s: %s failed: %s", run["eventid"], step_id, exc)
        step.update(status="failed", finding=f"Could not read it: {type(exc).__name__}: {exc}"[:240])
        return {"error": str(exc)[:240]}


def _run(run: dict) -> None:
    problem = run["problem"]
    code, name = problem["customer"], problem["device"]
    device = next((d for d in data.devices() if d["customer"] == code and d["name"] == name), None)
    evidence = {"problem": {"name": problem["name"], "severity": data.SEVERITIES.get(problem["severity"]),
                            "started": _iso(problem["started"]), "acknowledged": problem["acknowledged"],
                            "operational_data": problem.get("opdata") or "", "device": name,
                            "customer": problem.get("customer_name") or code, "site": problem.get("site")}}
    evidence["zabbix"] = _step(run, "zabbix", lambda: _zabbix(problem, device))
    evidence["nautobot"] = _step(run, "nautobot", lambda: _nautobot(device))
    evidence["glpi"] = _step(run, "glpi", lambda: _glpi(problem))
    evidence["golden_config"] = _step(run, "golden", lambda: _golden(device, code))
    evidence["semaphore"] = _step(run, "semaphore", lambda: _semaphore(code, name))
    templates = [t for t in data.automation(code)["templates"]
                 if t["name"] not in actions.change_only_templates() and t["limit"]]
    evidence["read_only_playbooks"] = [t["name"] for t in templates]
    try:
        answer = _ask(evidence)
        known = {d["name"] for d in data.devices() if d["customer"] == code}
        steps = []
        for item in (answer.get("next_steps") or [])[:4]:
            text = str((item or {}).get("text") or "").strip() if isinstance(item, dict) else str(item).strip()
            if not text:
                continue
            entry = {"text": text[:400]}
            playbook = item.get("playbook") if isinstance(item, dict) else None
            if isinstance(playbook, dict) and playbook.get("template") in evidence["read_only_playbooks"]:
                devices = [d for d in playbook.get("devices") or [] if d in known] or ([name] if name in known else [])
                if devices:
                    entry["playbook"] = {"template": playbook["template"], "devices": devices}
            steps.append(entry)
        confidence = str(answer.get("confidence") or "low").lower()
        # The audit trail first: whoever sees the run finished finds it in the trail too.
        store.update(run["entry"], "done", f"Draft written by {endpoint()[1]}; not posted until the engineer does.")
        run.update(status="done", cause=str(answer.get("cause") or "").strip()[:1500] or None,
                   confidence=confidence if confidence in ("low", "medium", "high") else "low", next_steps=steps,
                   note=_note(problem, answer))
    except Exception as exc:  # noqa: BLE001
        log.warning("Investigate %s: the model did not answer: %s", run["eventid"], exc)
        store.update(run["entry"], "failed", f"The model endpoint did not answer: {exc}"[:500])
        run.update(status="failed", cause=None, next_steps=[], note=None,
                   error=f"The model endpoint did not give a usable answer: {exc}"[:300])


def _zabbix(problem: dict, device: dict | None) -> tuple[str, dict, str | None]:
    if not device:
        return "The device is not in Nautobot any more.", {}, None
    host = data.hosts().get(device["id"])
    if not host:
        return "The device has no Zabbix host.", {}, None
    z = clients.zabbix()
    since = int((data.now() - timedelta(hours=24)).timestamp())
    events = z.call("event.get", {"hostids": [host["hostid"]], "time_from": since, "source": 0, "object": 0,
                                  "output": ["clock", "name", "severity", "value"], "sortfield": ["clock"],
                                  "sortorder": "DESC", "limit": 25})
    latest = {}
    for item in detail.items(host["hostid"]):
        if item["key_"] in ("icmpping", "icmppingloss", "icmppingsec", "system.cpu.util", "vm.memory.util",
                            "agent.ping", "zabbix[host,agent,available]") or item["key_"].startswith(
                                ("system.cpu.util[", "vm.memory.util", "net.if.status", "sensor", "system.uptime")):
            if item.get("lastclock") and int(item["lastclock"]) > 0:
                latest[item["name"]] = f"{item.get('lastvalue')} {item.get('units') or ''}".strip()
    changes = [{"at": _iso(datetime.fromtimestamp(int(e["clock"]), data.tz())), "event": e["name"],
                "state": "problem" if str(e["value"]) == "1" else "resolved"} for e in events]
    finding = (f"Host {host['state']}; {sum(1 for e in events if str(e['value']) == '1')} problem events in 24 hours; "
               f"{len(latest)} latest values read.")
    return finding, {"host_state": host["state"], "host_error": host["error"], "in_maintenance": host["maintenance"],
                     "events_24h": changes, "latest_values": dict(list(latest.items())[:30])}, None


def _nautobot(device: dict | None) -> tuple[str, dict, str | None]:
    if not device:
        return "The device is not in Nautobot.", {}, None
    ifaces = detail.interfaces(device)
    neighbours = [f"{i['name']} to {i['peer']['device']} {i['peer']['interface']}" for i in ifaces if i.get("peer")]
    link = f"{settings.public_url('nautobot')}/dcim/devices/{device['id']}/"
    finding = (f"{device['role'] or 'device'} {device['platform']} at {device['site']}; "
               f"{len(ifaces)} interfaces, {len(neighbours)} with a cable or circuit.")
    return finding, {"role": device["role"], "platform": device["platform"], "model": device["model"],
                     "site": device["site"], "address": device["ip"], "status": device["status"],
                     "interfaces": [{"name": i["name"], "status": i["status"], "description": i["description"],
                                     "in_bps": i["inBps"], "out_bps": i["outBps"], "errors_per_min": i["errorsPerMin"]}
                                    for i in ifaces[:40]],
                     "neighbours": neighbours}, link


def _glpi(problem: dict) -> tuple[str, dict, str | None]:
    ticket = str(problem.get("ticket_id") or "")
    if not ticket.isdigit():
        return "No GLPI ticket yet.", {}, None
    with clients.glpi() as g:
        item = g.get("Ticket", int(ticket))
        followups = g.request("GET", f"Ticket/{int(ticket)}/ITILFollowup") or []
    notes = [data.strip_html(f.get("content") or "")[:600] for f in followups[-8:]]
    return (f"Ticket #{ticket}, {data.TICKET_STATUS.get(int(item.get('status') or 0), '?')}, {len(followups)} follow-ups.",
            {"ticket": int(ticket), "title": item.get("name"), "status": data.TICKET_STATUS.get(int(item.get("status") or 0)),
             "description": data.strip_html(item.get("content") or "")[:1200], "recent_followups": notes},
            problem.get("ticket_link"))


def _golden(device: dict | None, code: str) -> tuple[str, dict, str | None]:
    if not device or not device["gc_scope"]:
        return "Golden Config does not look after this device.", {}, None
    back = data.backups().get(device["id"]) or {}
    rules = detail.compliance_rules(device)
    failed = [r for r in rules if not r["compliant"]]
    history = detail.backup_history(device, code)
    changed = [b for b in history if b["changedLines"]]
    finding = (f"Last backup {data.ago(back.get('last_success'))}; {len(failed)} of {len(rules)} rules not compliant; "
               f"{len(changed)} of the last {len(history)} backups changed the configuration.")
    return finding, {"last_backup": _iso(back.get("last_success")), "last_attempt": _iso(back.get("last_attempt")),
                     "failed_rules": [{"rule": r["rule"], "missing": r["missing"][:10], "extra": r["extra"][:10]}
                                      for r in failed],
                     "recent_backups": [{"at": _iso(b["at"]), "changed_lines": b["changedLines"], "message": b["message"]}
                                        for b in history[:6]]}, None


def _semaphore(code: str, name: str) -> tuple[str, dict, str | None]:
    tasks = [t for t in data.automation(code)["tasks"] if name in t["devices"]][:6]
    return (f"{len(tasks)} recent runs on the device." if tasks else "No recent playbook runs on the device.",
            [{"template": t["template"], "status": t["status"], "at": _iso(t["created"]), "item": t["item"]}
             for t in tasks], tasks[0]["link"] if tasks else None)


# =============================================================================== model
def _ask(evidence: dict) -> dict:
    url, model, key = endpoint()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": model, "temperature": 0.2,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps(evidence, default=str)[:60000]}]}
    session = make_session(retries=0)
    response = session.post(f"{url}/chat/completions", json={**body, "response_format": {"type": "json_object"}},
                            headers=headers, timeout=180)
    if response.status_code == 400:   # an endpoint that does not know response_format
        response = session.post(f"{url}/chat/completions", json=body, headers=headers, timeout=180)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
    content = response.json()["choices"][0]["message"]["content"] or ""
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise RuntimeError("the answer was not JSON")
    return json.loads(match.group(0))


def _note(problem: dict, answer: dict) -> str:
    note = str(answer.get("note") or "").strip()
    if not note:
        note = f"Likely cause: {answer.get('cause') or 'not clear from the evidence'}"
    return (f"{note}\n\n(Draft written with the MSP Console assistant from Zabbix, Nautobot, GLPI, Golden Config and "
            f"Semaphore data for problem {problem['eventid']}; checked by the engineer before posting.)")[:4000]
