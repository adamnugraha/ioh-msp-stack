"""Monthly customer report (kit 1.6.0): facts from the tools, text from the model, approval by an engineer.

On the first days of each month the console drafts last month's report for every customer (mappings.yaml,
section reports). A draft holds:

  facts      every figure, read from Zabbix (availability agreement, problems, path tests), GLPI (tickets),
             Golden Config (compliance, backups) and msp-glue's capacity forecast. The tables of the report are
             printed from these, never from the model.
  narrative  short paragraphs per section and up to three recommendations, written by the console's model
             endpoint (CONSOLE_AI_URL, CONSOLE_AI_MODEL: the one Investigate uses) in the customer's language
             (tenant field msp_report_language: id or en). Without an endpoint, or when it fails, fixed sentences
             are used instead.
  flags      numbers in the narrative that the facts do not contain, so the engineer checks them.

An engineer (or a manager) reads the draft in the console, corrects the text if needed and approves it; only an
approved report prints without the "draft" mark. Every step goes to the audit trail. Nothing leaves the platform:
the model is the one IOH runs or has approved.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import clients
from ..settings import settings
from . import actions, data, investigate, store
from .auth import User

log = logging.getLogger("msp_glue.console.reports")
HERE = Path(__file__).resolve().parent
SECTIONS = ("summary", "availability", "service", "paths", "capacity")
SYSTEM_USER = {"sub": "", "email": "msp-console", "name": "MSP Console (schedule)", "roles": []}
_busy: set[tuple[str, str]] = set()
_lock = threading.Lock()

MONTHS = {
    "id": ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
           "November", "Desember"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
}


def _config() -> dict:
    return settings.mappings.get("reports") or {}


def month_label(month: str, language: str = "en") -> str:
    year, number = month.split("-")
    return f"{MONTHS.get(language, MONTHS['en'])[int(number) - 1]} {year}"


def previous_month(moment: datetime | None = None) -> str:
    moment = moment or data.now()
    return (moment.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=data.tz())
    return start, data.next_month(start)


def language_of(tenant) -> str:
    chosen = str((tenant.custom_fields or {}).get("msp_report_language") or _config().get("default_language") or "id")
    return chosen if chosen in ("id", "en") else "id"


# ================================================================================= facts
def _num(value, digits: int = 2):
    return None if value is None else round(float(value), digits)


def facts(code: str, month: str) -> dict:
    tenant = data.customer(code)
    if not tenant:
        raise actions.ActionError(f"No managed customer with code {code}.", status=404)
    start, end = _month_bounds(month)
    language = language_of(tenant)
    row = next((r for r in data.service_rows(start) if r["code"] == code), {})
    avail = data.availability().get(code, {})
    periods = {p["month"]: p for p in avail.get("periods", [])}
    before = (start - timedelta(days=1)).strftime("%Y-%m")
    period = periods.get(month, {})
    downtime = period.get("downtime") if period else row.get("downtime")
    out = {
        "customer": {"code": tenant.code, "name": tenant.name, "tier": tenant.tier, "regulated": tenant.regulated},
        "period": {"month": month, "label": month_label(month, language), "start": start.isoformat(),
                   "end": end.isoformat()},
        "language": language,
        "availability": {
            "target_percent": avail.get("slo"),
            "achieved_percent": period.get("sli") if period else row.get("sli"),
            "previous_month_percent": (periods.get(before) or {}).get("sli"),
            "downtime_minutes": _num(downtime / 60, 1) if downtime is not None else None,
        },
        "tickets": {
            "opened": row.get("opened", 0), "solved": row.get("solved", 0),
            "service_level_breaches": row.get("month_late", 0), "taken_late": row.get("own_late", 0),
            "resolved_late": row.get("resolve_late", 0), "mean_hours_to_resolve": row.get("mttr"),
        },
        "configuration": {
            "devices_checked": row.get("checked_n", 0), "compliant_percent": row.get("compliance"),
            "backups_fresh_percent": row.get("backup"), "as_of": data.now().isoformat(timespec="minutes"),
        },
        "devices": {"active": row.get("active", 0)},
    }
    a = out["availability"]
    if a["target_percent"] is not None and a["achieved_percent"] is not None:
        a["met"] = a["achieved_percent"] >= a["target_percent"]
    for name, loader in (("problems", lambda: _problems(code, start, end)), ("paths", lambda: _paths(code, start, end)),
                         ("capacity", lambda: _capacity(code))):
        try:
            out[name] = loader()
        except Exception as exc:  # noqa: BLE001 - one source down: say so in the facts, keep the others
            log.warning("Report %s %s: %s not read: %s", code, month, name, exc)
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return out


def _problems(code: str, start: datetime, end: datetime) -> dict:
    """Problems of Average or worse on the customer's managed devices that started in the month."""
    z = clients.zabbix()
    events = z.call("event.get", {
        "source": 0, "object": 0, "value": 1, "severities": [3, 4, 5],
        "time_from": int(start.timestamp()), "time_till": int(end.timestamp()) - 1,
        "tags": [{"tag": "managed_by", "value": "msp-glue", "operator": 1}, {"tag": "tenant", "value": code, "operator": 1}],
        "output": ["eventid", "clock", "name", "severity", "r_eventid"], "selectTags": ["tag", "value"],
        "sortfield": ["clock"], "sortorder": "ASC", "limit": 5000})
    recovered = {e["eventid"]: int(e["clock"]) for e in z.call("event.get", {
        "eventids": [e["r_eventid"] for e in events if e.get("r_eventid") not in (None, "", "0")],
        "output": ["eventid", "clock"]})} if any(e.get("r_eventid") not in (None, "", "0") for e in events) else {}
    stop = int(min(end, data.now()).timestamp())
    rows, counts = [], {}
    for e in events:
        tags = {t["tag"]: t["value"] for t in e.get("tags", [])}
        severity = data.SEVERITIES.get(int(e["severity"]), "?")
        counts[severity] = counts.get(severity, 0) + 1
        finished = recovered.get(e.get("r_eventid"))
        minutes = round(((finished or stop) - int(e["clock"])) / 60)
        rows.append({"device": tags.get("device", ""), "problem": e["name"], "severity": severity,
                     "started": datetime.fromtimestamp(int(e["clock"]), data.tz()).isoformat(timespec="minutes"),
                     "minutes": minutes, "resolved": finished is not None})
    rows.sort(key=lambda r: -r["minutes"])
    return {"count": len(events), "by_severity": counts, "longest": rows[:5],
            "total_minutes": sum(r["minutes"] for r in rows)}


def _paths(code: str, start: datetime, end: datetime) -> list[dict]:
    from .. import paths as path_tests
    z = clients.zabbix()
    hosts = path_tests.managed_path_hosts(z, code)
    if not hosts:
        return []
    by_id = {h["hostid"]: {t["tag"]: t["value"] for t in h.get("tags", [])} for h in hosts}
    keys = {"icmppingsec[,10]": "delay", "icmppingloss[,10]": "loss", "msp.path.https.time": "web",
            "net.tcp.service.perf[tcp,,{$PATH.PORT}]": "connect"}
    items = [i for i in z.call("item.get", {"hostids": list(by_id), "output": ["itemid", "hostid", "key_"]})
             if i["key_"] in keys]
    trends: dict[str, list[float]] = {}
    for start_index in range(0, len(items), 50):
        for r in z.call("trend.get", {"itemids": [i["itemid"] for i in items[start_index:start_index + 50]],
                                      "time_from": int(start.timestamp()), "time_till": int(end.timestamp()) - 1,
                                      "output": ["itemid", "value_avg"]}):
            trends.setdefault(r["itemid"], []).append(float(r["value_avg"]))
    problems: dict[str, int] = {}
    for e in z.call("event.get", {
            "source": 0, "object": 0, "value": 1, "severities": [2, 3, 4, 5],
            "time_from": int(start.timestamp()), "time_till": int(end.timestamp()) - 1, "hostids": list(by_id),
            "output": ["eventid"], "selectHosts": ["hostid"]}):
        for h in e.get("hosts", []):
            problems[h["hostid"]] = problems.get(h["hostid"], 0) + 1
    rows: dict[str, dict] = {}
    for hostid, tags in by_id.items():
        rows[hostid] = {"collector": tags.get("collector", ""), "target": tags.get("path_target", ""),
                        "segment": tags.get("segment", ""), "problems": problems.get(hostid, 0)}
    for item in items:
        values = trends.get(item["itemid"]) or []
        if not values:
            continue
        kind, row = keys[item["key_"]], rows[item["hostid"]]
        if kind == "loss":
            row["loss_percent"] = round(sum(values) / len(values), 2)
        else:
            row[f"{kind}_ms"] = round(1000 * sum(values) / len(values), 1)
            row[f"worst_hour_{kind}_ms"] = round(1000 * max(values), 1)
    return sorted(rows.values(), key=lambda r: (r["collector"], ("ioh", "internet", "application").index(r["segment"])
                                                if r["segment"] in ("ioh", "internet", "application") else 3, r["target"]))


def _capacity(code: str) -> list[dict]:
    from .. import capacity
    links = [x for x in capacity.load().get("links", []) if x.get("customer") == code
             and x.get("status") in ("over", "at_risk", "ok")]
    links.sort(key=lambda x: ({"over": 0, "at_risk": 1}.get(x["status"], 2), -(x.get("utilisation") or 0)))
    return [{"device": x["device"], "interface": x["interface"], "circuit": x.get("circuit") or "",
             "capacity_mbps": _num((x.get("capacity_bps") or 0) / 1e6, 1),
             "busy_hour_mbps": _num((x.get("busy_bps") or 0) / 1e6, 1), "utilisation_percent": x.get("utilisation"),
             "days_to_warning": x.get("days_to_warn"), "status": x["status"]} for x in links[:6]]


# ========================================================================= writing
SYSTEM = {
    "id": ("Anda menulis laporan layanan bulanan dari IOH (Indosat Ooredoo Hutchison) Managed Services untuk "
           "pelanggannya. Tulis dalam Bahasa Indonesia baku yang lugas dan sopan, seperti laporan resmi kepada "
           "pelanggan korporat."),
    "en": ("You write the monthly service report from IOH (Indosat Ooredoo Hutchison) Managed Services to one of its "
           "customers. Write in plain, formal British English, as a report to a corporate customer."),
}
RULES = (
    "Use only the facts in the JSON you receive. Every number you write must appear in the facts (rounding to fewer "
    "decimals is fine); never invent devices, events, causes or figures. When a section has no data, return an empty "
    "string for it. No marketing words, no exclamation marks, no headings inside the text. Answer with one JSON "
    "object and nothing else, with the keys: summary (three or four sentences on the month as a whole), availability "
    "(one to three sentences: achieved against target, downtime, the longest problems), service (one to three "
    "sentences on tickets and service levels), paths (one to three sentences on path test quality, if there are path "
    "tests), capacity (one or two sentences on links close to their capacity, if any), recommendations (a list of at "
    "most three short sentences, only where the facts support them, for example a bandwidth review for a link at risk)."
)


def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def ask_model(facts_: dict) -> dict:
    url, model, key = investigate.endpoint()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    language = facts_.get("language", "id")
    content = json.dumps(facts_, ensure_ascii=False, default=str)[:40000]
    if model.lower().startswith("qwen3"):
        content += "\n/no_think"     # Qwen3 thinks aloud by default: slow on a processor, and not needed here
    body = {"model": model, "temperature": 0.2,
            "messages": [{"role": "system", "content": f"{SYSTEM.get(language, SYSTEM['en'])} {RULES}"},
                         {"role": "user", "content": content}]}
    from ..http import make_session
    session = make_session(retries=0)
    response = session.post(f"{url}/chat/completions", json={**body, "response_format": {"type": "json_object"}},
                            headers=headers, timeout=900)
    if response.status_code == 400:   # an endpoint that does not know response_format
        response = session.post(f"{url}/chat/completions", json=body, headers=headers, timeout=900)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
    content = _strip_thinking(response.json()["choices"][0]["message"]["content"] or "")
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise RuntimeError("the answer was not JSON")
    answer = json.loads(match.group(0))
    out = {s: str(answer.get(s) or "").strip()[:1500] for s in SECTIONS}
    recs = answer.get("recommendations") or []
    out["recommendations"] = [str(r).strip()[:400] for r in (recs if isinstance(recs, list) else [recs]) if str(r).strip()][:3]
    return out


def fmt(value, language: str, digits: int = 2) -> str:
    try:
        number = float(value)
    except Exception:  # noqa: BLE001 - None, a missing value in the page template, or text
        return "-"
    text = f"{number:,.{digits}f}"
    if digits:
        text = text.rstrip("0").rstrip(".")
    if language == "id":
        text = text.replace(",", "_").replace(".", ",").replace("_", ".")
    return text


def _warn() -> float:
    return float((settings.mappings.get("capacity") or {}).get("warn_percent", 80))


def fixed_text(f: dict) -> dict:
    """The narrative in fixed sentences, for when no model is set or it did not answer."""
    lang = f.get("language", "id")
    a, t, period = f["availability"], f["tickets"], f["period"]["label"]
    p = f.get("problems") if isinstance(f.get("problems"), dict) else {}
    paths = f.get("paths") if isinstance(f.get("paths"), list) else []
    cap = f.get("capacity") if isinstance(f.get("capacity"), list) else []
    risky = [c for c in cap if c["status"] in ("over", "at_risk")]
    bad_paths = [x for x in paths if x.get("problems")]
    n = lambda v, d=2: fmt(v, lang, d)  # noqa: E731
    if lang == "id":
        if a.get("achieved_percent") is not None:
            avail = (f"Ketersediaan layanan pada {period} mencapai {n(a['achieved_percent'], 3)}% dari target "
                     f"{n(a['target_percent'])}%, dengan total gangguan {n(a.get('downtime_minutes'), 1)} menit.")
            if a.get("met") is False:
                avail += " Angka ini berada di bawah target yang disepakati."
        else:
            avail = f"Data ketersediaan untuk {period} belum tersedia."
        if p.get("count"):
            avail += f" Tercatat {p['count']} gangguan dengan tingkat Average atau lebih tinggi pada perangkat terkelola."
        service = (f"Sebanyak {t['opened']} tiket dibuka dan {t['solved']} diselesaikan pada bulan ini"
                   + (f", dengan rata-rata waktu penyelesaian {n(t['mean_hours_to_resolve'], 1)} jam" if t.get("mean_hours_to_resolve") is not None else "")
                   + (f"; {t['service_level_breaches']} tiket melewati batas waktu layanan." if t.get("service_level_breaches") else
                      "; seluruh tiket ditangani dalam batas waktu layanan."))
        path_text = ""
        if paths:
            path_text = (f"Uji jalur dari lokasi pelanggan memeriksa {len(paths)} tujuan; "
                         + (f"{len(bad_paths)} di antaranya sempat mencatat masalah." if bad_paths else
                            "tidak ada masalah yang tercatat."))
        cap_text = ""
        if risky:
            cap_text = f"{len(risky)} jalur mendekati atau melampaui {n(_warn(), 0)}% kapasitas pada jam tersibuk."
        summary = " ".join(x for x in (avail.split(". ")[0].rstrip(".") + ".", service.split(";")[0] + ".", cap_text) if x)
        recs = [f"Kami menyarankan peninjauan kapasitas jalur {c['interface']} pada {c['device']}." for c in risky[:2]]
        if a.get("met") is False:
            recs.append("Kami akan menyampaikan analisis penyebab gangguan terpanjang bulan ini.")
    else:
        if a.get("achieved_percent") is not None:
            avail = (f"Availability in {period} was {n(a['achieved_percent'], 3)}% against a target of "
                     f"{n(a['target_percent'])}%, with {n(a.get('downtime_minutes'), 1)} minutes of downtime.")
            if a.get("met") is False:
                avail += " This is below the agreed target."
        else:
            avail = f"Availability data for {period} is not available yet."
        if p.get("count"):
            avail += f" {p['count']} problems of Average severity or higher were recorded on managed devices."
        service = (f"{t['opened']} tickets were opened and {t['solved']} solved this month"
                   + (f", with a mean time to resolve of {n(t['mean_hours_to_resolve'], 1)} hours" if t.get("mean_hours_to_resolve") is not None else "")
                   + (f"; {t['service_level_breaches']} tickets missed their service level." if t.get("service_level_breaches") else
                      "; every ticket was handled within its service level."))
        path_text = ""
        if paths:
            path_text = (f"Path tests from the customer's sites checked {len(paths)} targets; "
                         + (f"{len(bad_paths)} of them recorded problems." if bad_paths else "none recorded a problem."))
        cap_text = ""
        if risky:
            cap_text = f"{len(risky)} links are close to or above {n(_warn(), 0)}% of capacity at their busy hour."
        summary = " ".join(x for x in (avail.split(". ")[0].rstrip(".") + ".", service.split(";")[0] + ".", cap_text) if x)
        recs = [f"We recommend a capacity review of {c['interface']} on {c['device']}." for c in risky[:2]]
        if a.get("met") is False:
            recs.append("We will share an analysis of the cause of this month's longest outage.")
    return {"summary": summary, "availability": avail, "service": service, "paths": path_text, "capacity": cap_text,
            "recommendations": recs[:3]}


# ===================================================================== number check
_NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?![\w])")


def _fact_numbers(value, out: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(float(value))
        return
    if isinstance(value, dict):
        for v in value.values():
            _fact_numbers(v, out)
    elif isinstance(value, list):
        out.add(float(len(value)))
        for v in value:
            _fact_numbers(v, out)
    elif isinstance(value, str):
        stamp = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?(?:T(\d{2}):(\d{2}))?", value)
        if stamp:   # a date or time: its year, month, day, hour and minute may be written out (not its zone)
            out.update(float(part) for part in stamp.groups() if part)
        else:       # a label such as "Oktober 2026": numbers standing on their own, not the 01 of rtr01
            out.update(float(c[0]) for token in _NUMBER.findall(value) for c in _candidates(token))


def _candidates(token: str) -> list[tuple[float, int]]:
    """Possible readings of a number as written, with its decimals: 99,95 and 99.95, 1.234 and 1,234."""
    out = []
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):      # 1.234,5 (Indonesian)
            whole, _, frac = token.replace(".", "").partition(",")
        else:                                       # 1,234.5 (English)
            whole, _, frac = token.replace(",", "").partition(".")
        out.append((float(f"{whole}.{frac or 0}"), len(frac)))
    elif "," in token or "." in token:
        sep = "," if "," in token else "."
        head, _, tail = token.rpartition(sep)
        out.append((float(token.replace(sep, ".", 1)) if token.count(sep) == 1 else float(token.replace(sep, "")),
                    len(tail) if token.count(sep) == 1 else 0))
        if len(tail) == 3:                          # thousands separator
            out.append((float(token.replace(sep, "")), 0))
    else:
        out.append((float(token), 0))
    return out


def check_numbers(narrative: dict, facts_: dict) -> list[dict]:
    known: set[float] = set()
    _fact_numbers(facts_, known)
    rounded = {(round(v, d), d) for v in known for d in range(0, 4)}
    rounded |= {(_warn(), 0)}
    flags = []
    for section in (*SECTIONS, "recommendations"):
        texts = narrative.get(section) or []
        for text in (texts if isinstance(texts, list) else [texts]):
            for token in _NUMBER.findall(text or ""):
                if not any((round(value, digits), digits) in rounded for value, digits in _candidates(token)):
                    flags.append({"section": section, "number": token})
    return flags


# =================================================================== the reports
def _row_out(r) -> dict:
    out = dict(r)
    for name in ("facts", "narrative", "flags"):
        out[name] = json.loads(out[name] or ("[]" if name == "flags" else "{}"))
    out["created"] = datetime.fromtimestamp(out["created"], data.tz())
    out["updated"] = datetime.fromtimestamp(out["updated"], data.tz())
    out["approved_at"] = datetime.fromtimestamp(out["approved_at"], data.tz()) if out["approved_at"] else None
    out["busy"] = (out["customer"], out["month"]) in _busy
    return out


def get(code: str, month: str) -> dict | None:
    with store.db() as conn:
        r = conn.execute("SELECT * FROM reports WHERE customer = ? AND month = ?", (code, month)).fetchone()
    return _row_out(r) if r else None


def month_rows(month: str) -> list[dict]:
    with store.db() as conn:
        rows = conn.execute("SELECT * FROM reports WHERE month = ?", (month,)).fetchall()
    return [_row_out(r) for r in rows]


def _write_row(code: str, month: str, **values) -> None:
    now = time.time()
    with store.db() as conn:
        exists = conn.execute("SELECT 1 FROM reports WHERE customer = ? AND month = ?", (code, month)).fetchone()
        for name in ("facts", "narrative", "flags"):
            if name in values and not isinstance(values[name], str):
                values[name] = json.dumps(values[name], ensure_ascii=False, default=str)
        if exists:
            sets = ", ".join(f"{k} = ?" for k in values) + ", updated = ?"
            conn.execute(f"UPDATE reports SET {sets} WHERE customer = ? AND month = ?",
                         (*values.values(), now, code, month))
        else:
            columns = ["customer", "month", "created", "updated", *values]
            conn.execute(f"INSERT INTO reports ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                         (code, month, now, now, *values.values()))


def generate(code: str, month: str, user: dict | None = None, entry: int | None = None) -> dict:
    """Build the facts, write the narrative and store the draft (runs in a background thread)."""
    user = user or SYSTEM_USER
    try:
        f = facts(code, month)
        writer, note = "fixed sentences", ""
        narrative = None
        if investigate.enabled():
            try:
                narrative = ask_model(f)
                writer = investigate.endpoint()[1]
            except Exception as exc:  # noqa: BLE001 - fall back to the fixed sentences, and say why
                note = f"The model endpoint did not answer ({exc}); the text uses fixed sentences."[:400]
                log.warning("Report %s %s: %s", code, month, note)
        if narrative is None:
            narrative = fixed_text(f)
        flags = check_numbers(narrative, f)
        _write_row(code, month, status="draft", language=f["language"], facts=f, narrative=narrative, flags=flags,
                   writer=writer, error=note, edited_by="", approved_by="", approved_at=None)
        if entry:
            store.update(entry, "done", f"Draft written ({writer}); {len(flags)} numbers to check.")
        else:
            store.record(user, "report draft", customer=code, target=month, outcome="done",
                         result=f"Draft written ({writer}); {len(flags)} numbers to check.")
    except Exception as exc:  # noqa: BLE001 - keep the reason for the page
        log.exception("Report %s %s failed", code, month)
        _write_row(code, month, status="failed", error=f"{type(exc).__name__}: {exc}"[:400])
        if entry:
            store.update(entry, "failed", f"{type(exc).__name__}: {exc}"[:500])
    finally:
        with _lock:
            _busy.discard((code, month))
    return get(code, month) or {}


def _can_report(user: User) -> None:
    if not (user.can_act or user.is_manager):
        raise actions.Forbidden("Monthly reports need the engineer or manager role.")


def _valid_month(month: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise actions.ActionError("The month must look like 2026-10.")
    if month >= data.now().strftime("%Y-%m"):
        raise actions.ActionError("A report can only be drafted for a month that has ended.")
    return month


def start(user: User, code: str, month: str, address: str = "") -> dict:
    _can_report(user)
    month = _valid_month(month)
    if not data.customer(code):
        raise actions.ActionError(f"No managed customer with code {code}.", status=404)
    current = get(code, month)
    if current and current["status"] == "approved":
        raise actions.ActionError("This report is approved. Reopen it before drafting it again.", status=409)
    with _lock:
        if (code, month) in _busy:
            raise actions.ActionError("This report is being drafted already.", status=409)
        _busy.add((code, month))
    entry = store.record(user.as_dict(), "report draft", customer=code, target=month, outcome="started",
                         address=address)
    _write_row(code, month, status="drafting", error="")
    threading.Thread(target=generate, args=(code, month, user.as_dict(), entry), daemon=True,
                     name=f"report {code} {month}").start()
    return {"message": f"Drafting the {month_label(month)} report for {code}. It takes a few minutes with a model.",
            "auditId": entry}


def save_text(user: User, code: str, month: str, narrative: dict, address: str = "") -> dict:
    _can_report(user)
    current = get(code, month)
    if not current:
        raise actions.ActionError("There is no draft for this month yet.", status=404)
    if current["status"] != "draft":
        raise actions.ActionError("Only a draft can be edited. Reopen an approved report first.", status=409)
    clean = {s: str(narrative.get(s) or "").strip()[:3000] for s in SECTIONS}
    recs = narrative.get("recommendations") or []
    clean["recommendations"] = [str(r).strip()[:600] for r in (recs if isinstance(recs, list) else [recs])
                                if str(r).strip()][:5]
    flags = check_numbers(clean, current["facts"])
    _write_row(code, month, narrative=clean, flags=flags, edited_by=user.name or user.email)
    entry = store.record(user.as_dict(), "report edit", customer=code, target=month, outcome="done",
                         result=f"{len(flags)} numbers to check", address=address)
    return {"message": "Text saved." + (f" {len(flags)} numbers are not in the data; check them before approving."
                                        if flags else ""), "auditId": entry}


def approve(user: User, code: str, month: str, address: str = "") -> dict:
    _can_report(user)
    current = get(code, month)
    if not current or current["status"] != "draft":
        raise actions.ActionError("Only a finished draft can be approved.", status=409)
    _write_row(code, month, status="approved", approved_by=user.name or user.email,
               approved_at=data.now().timestamp())
    entry = store.record(user.as_dict(), "report approve", customer=code, target=month, outcome="done",
                         result=f"Approved with {len(current['flags'])} numbers flagged" if current["flags"] else "Approved",
                         address=address)
    return {"message": f"Approved. The {month_label(month)} report for {code} prints without the draft mark now.",
            "auditId": entry}


def reopen(user: User, code: str, month: str, address: str = "") -> dict:
    _can_report(user)
    current = get(code, month)
    if not current or current["status"] != "approved":
        raise actions.ActionError("Only an approved report can be reopened.", status=409)
    _write_row(code, month, status="draft", approved_by="", approved_at=None)
    entry = store.record(user.as_dict(), "report reopen", customer=code, target=month, outcome="done", address=address)
    return {"message": "Reopened as a draft.", "auditId": entry}


# ======================================================================= schedule
def generate_due(moment: datetime | None = None) -> list[str]:
    """Draft last month's report for every customer that has none, once the configured day and hour pass."""
    cfg = _config()
    moment = moment or data.now()
    if moment.day < int(cfg.get("draft_day", 1)) or moment.hour < int(cfg.get("hour", 2)):
        return []
    month = previous_month(moment)
    have = {r["customer"] for r in month_rows(month)}
    done = []
    for tenant in data.customers():
        if tenant.code in have:
            continue
        with _lock:
            if (tenant.code, month) in _busy:
                continue
            _busy.add((tenant.code, month))
        _write_row(tenant.code, month, status="drafting", error="")
        generate(tenant.code, month)
        done.append(tenant.code)
    return done


def scheduler() -> None:
    time.sleep(90)
    while True:
        try:
            drafted = generate_due()
            if drafted:
                log.info("Monthly reports drafted for %s", ", ".join(drafted))
        except Exception:  # noqa: BLE001 - try again later
            log.exception("Monthly report schedule failed")
        time.sleep(1800)


# ========================================================================== pages
def summary(month: str | None) -> dict:
    current = data.now().strftime("%Y-%m")
    month = month if month and re.fullmatch(r"\d{4}-\d{2}", month) and month < current else previous_month()
    rows = {r["customer"]: r for r in month_rows(month)}
    out = []
    for t in sorted(data.customers(), key=lambda t: t.name):
        r = rows.get(t.code)
        out.append({"code": t.code, "name": t.name, "language": r["language"] if r else language_of(t),
                    "status": "drafting" if r and r["busy"] else (r["status"] if r else "none"),
                    "flags": len(r["flags"]) if r else 0, "writer": r["writer"] if r else "",
                    "updated": r["updated"].isoformat(timespec="seconds") if r else None,
                    "approvedBy": r["approved_by"] if r else "", "error": r["error"] if r else ""})
    months, moment = [], data.month_start(1)
    for _ in range(12):
        months.append({"month": moment.strftime("%Y-%m"), "label": month_label(moment.strftime("%Y-%m"))})
        moment = (moment - timedelta(days=1)).replace(day=1)
    url, model, _key = investigate.endpoint()
    return {"month": month, "label": month_label(month), "monthsAvailable": months, "rows": out,
            "writer": {"model": model if url and model else None}}


def detail(code: str, month: str) -> dict | None:
    tenant = data.customer(code)
    r = get(code, month)
    if not tenant:
        return None
    if not r:
        return {"code": code, "name": tenant.name, "month": month, "label": month_label(month), "status": "none"}
    return {"code": code, "name": tenant.name, "month": month, "label": month_label(month, r["language"]),
            "status": "drafting" if r["busy"] else r["status"], "language": r["language"], "facts": r["facts"],
            "narrative": r["narrative"], "flags": r["flags"], "writer": r["writer"], "error": r["error"],
            "created": r["created"].isoformat(timespec="seconds"), "updated": r["updated"].isoformat(timespec="seconds"),
            "editedBy": r["edited_by"], "approvedBy": r["approved_by"],
            "approvedAt": r["approved_at"].isoformat(timespec="seconds") if r["approved_at"] else None}


WORDS = {
    "id": {"title": "Laporan Layanan Bulanan", "draft": "DRAF: belum disetujui", "summary": "Ringkasan",
           "availability": "Ketersediaan layanan", "service": "Tiket dan tingkat layanan", "paths": "Kualitas jalur",
           "capacity": "Kapasitas jalur", "configuration": "Konfigurasi perangkat", "recommendations": "Rekomendasi",
           "target": "Target", "achieved": "Tercapai", "downtime": "Gangguan (menit)", "previous": "Bulan sebelumnya",
           "opened": "Tiket dibuka", "solved": "Diselesaikan", "breaches": "Melewati batas waktu",
           "mttr": "Rata-rata penyelesaian (jam)", "longest": "Gangguan terpanjang", "device": "Perangkat",
           "problem": "Masalah", "started": "Mulai", "minutes": "Menit", "collector": "Lokasi uji", "target_col": "Tujuan",
           "segment": "Segmen", "delay": "Jeda rata-rata (ms)", "loss": "Paket hilang (%)", "web": "Respons web (ms)",
           "issues": "Masalah", "link": "Jalur", "capacity_col": "Kapasitas (Mbps)", "busy": "Jam tersibuk (Mbps)",
           "use": "Pemakaian (%)", "days": "Hari menuju batas", "checked": "Perangkat diperiksa",
           "compliant": "Sesuai standar (%)", "backups": "Cadangan terbaru (%)",
           "segments": {"ioh": "Jaringan IOH", "internet": "Internet", "application": "Aplikasi"},
           "footer": "Disusun oleh IOH Managed Services dari data pemantauan dan layanan.",
           "approved": "Diperiksa dan disetujui oleh {who} pada {when}.", "model": "Teks disusun dengan bantuan model bahasa"},
    "en": {"title": "Monthly Service Report", "draft": "DRAFT: not approved", "summary": "Summary",
           "availability": "Availability", "service": "Tickets and service levels", "paths": "Path quality",
           "capacity": "Link capacity", "configuration": "Device configuration", "recommendations": "Recommendations",
           "target": "Target", "achieved": "Achieved", "downtime": "Downtime (minutes)", "previous": "Previous month",
           "opened": "Tickets opened", "solved": "Solved", "breaches": "Service level missed",
           "mttr": "Mean time to resolve (hours)", "longest": "Longest problems", "device": "Device",
           "problem": "Problem", "started": "Started", "minutes": "Minutes", "collector": "Test site", "target_col": "Target",
           "segment": "Segment", "delay": "Average delay (ms)", "loss": "Packet loss (%)", "web": "Web response (ms)",
           "issues": "Problems", "link": "Link", "capacity_col": "Capacity (Mbps)", "busy": "Busy hour (Mbps)",
           "use": "Use (%)", "days": "Days to warning", "checked": "Devices checked",
           "compliant": "Compliant (%)", "backups": "Recent backups (%)",
           "segments": {"ioh": "IOH network", "internet": "Internet", "application": "Application"},
           "footer": "Prepared by IOH Managed Services from monitoring and service data.",
           "approved": "Checked and approved by {who} on {when}.", "model": "Text drafted with the help of a language model"},
}
_env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]),
                   trim_blocks=True, lstrip_blocks=True)


def render(code: str, month: str) -> str | None:
    r = get(code, month)
    tenant = data.customer(code)
    if not r or not tenant or r["status"] not in ("draft", "approved"):
        return None
    lang = r["language"] if r["language"] in WORDS else "en"
    when = r["approved_at"].strftime("%d-%m-%Y") if r["approved_at"] else ""
    branding = Path(os.environ.get("CONSOLE_BRANDING_DIR", "/branding"))
    logo = any((branding / name).is_file() for name in ("logo.svg", "logo.png"))
    return _env.get_template("report.html").render(
        r=r, f=r["facts"], n=r["narrative"], w=WORDS[lang], lang=lang, name=tenant.name,
        label=month_label(month, lang), draft=r["status"] != "approved",
        approved=WORDS[lang]["approved"].format(who=r["approved_by"], when=when) if r["approved_at"] else "",
        num=lambda v, d=2: fmt(v, lang, d), used_model=r["writer"] not in ("", "fixed sentences"), logo=logo)
