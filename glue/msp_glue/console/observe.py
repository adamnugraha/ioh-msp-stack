"""Path tests and capacity forecasts for the MSP Console pages (kit 1.6.0). Read-only, cached like the others.

Path tests: per site collector, the latest values of each target and the verdict: on which side of the path a
slowdown is (IOH's link to the site, beyond IOH's edge, or the application provider). Capacity: msp-glue's daily
forecast (capacity.json), per customer or the links at risk across every customer.
"""

from __future__ import annotations

import time
from datetime import datetime

from .. import capacity as capacity_forecast
from .. import clients
from .. import paths as path_tests
from ..settings import settings
from . import data

ORDER = {"bad": 0, "warn": 1, "unknown": 2, "info": 3, "ok": 4}
KEYS = {"icmppingsec[,10]": "delay", "icmppingloss[,10]": "loss", "net.tcp.service.perf[tcp,,{$PATH.PORT}]": "connect",
        "msp.path.https.time": "web", "msp.path.https.status": "status", "msp.path.https.error": "error",
        "msp.path.https.ok": "ok"}
FRESH = {"delay": 900, "loss": 900, "connect": 900, "web": 1800}


def _target_state(problems: list[dict], values: dict, now: float) -> str:
    if problems:
        worst = max(p["severity"] for p in problems)
        return "bad" if worst >= 3 else "warn" if worst == 2 else "info"
    fresh = [k for k, limit in FRESH.items() if k in values and now - values[k]["clock"] <= limit]
    return "ok" if fresh else "unknown"


def segment_states(targets: list[dict]) -> dict[str, str]:
    """IOH's edge counts as bad when any of its targets is; the internet only when every reference point is (one
    public address that ignores ping says nothing about the path); applications one by one."""
    out = {}
    for segment in path_tests.SEGMENTS:
        mine = [t["state"] for t in targets if t["segment"] == segment]
        if not mine:
            continue
        troubled = [s for s in mine if s in ("bad", "warn")]
        if segment == "internet" and troubled and len(troubled) < len(mine):
            out[segment] = "partial"
        else:
            out[segment] = min(mine, key=lambda s: ORDER.get(s, 9))
    return out


def verdict(targets: list[dict], online: bool) -> dict:
    states = segment_states(targets)
    trouble = lambda s: states.get(s) in ("bad", "warn")  # noqa: E731
    if not online:
        return {"side": "collector", "tone": "warn",
                "text": "The site collector is not sending data, so the path cannot be judged. Check the collector "
                        "and its outbound connection on port 10051."}
    if "ioh" in states and trouble("ioh"):
        return {"side": "ioh", "tone": "bad",
                "text": "Between the site and IOH's edge router: IOH's access link, or the customer's own network at the site."}
    if trouble("internet"):
        if "ioh" in states:
            return {"side": "internet", "tone": "bad",
                    "text": "Beyond IOH's edge router, on the way to the internet. The link to the site itself is fine."}
        return {"side": "internet", "tone": "bad",
                "text": "Between the site and the internet. Add IOH's edge router at the site as a target to tell "
                        "IOH's link apart from the rest of the way."}
    apps = [t["name"] for t in targets if t["segment"] == "application" and t["state"] in ("bad", "warn")]
    if apps:
        return {"side": "application", "tone": "warn",
                "text": f"At the application provider ({', '.join(apps)}): the path to the internet is fine."}
    if targets and all(t["state"] == "unknown" for t in targets):
        return {"side": "none", "tone": "neutral",
                "text": "No results yet. The first values arrive a few minutes after a target is added."}
    note = "" if "ioh" in states else " Add IOH's edge router at the site as a target to watch IOH's link on its own."
    return {"side": "none", "tone": "ok", "text": "Every part of the path is fine." + note}


def paths(code: str) -> dict:
    tenant = data.customer(code)

    def load():
        z = clients.zabbix()
        hosts = z.call("host.get", {
            "output": ["hostid", "name", "status"], "evaltype": 0,
            "tags": [{"tag": "managed_by", "value": path_tests.MANAGED_VALUE, "operator": 1},
                     {"tag": "tenant", "value": code, "operator": 1}],
            "selectTags": ["tag", "value"], "selectMacros": ["macro", "value"],
            "selectParentTemplates": ["host"], "selectInterfaces": ["ip", "dns", "useip", "main"]})
        hosts = [h for h in hosts if str(h["status"]) == "0"]
        ids = [h["hostid"] for h in hosts]
        items = z.call("item.get", {"hostids": ids, "output": ["itemid", "hostid", "key_", "lastvalue", "lastclock",
                                                                "state", "error"]}) if ids else []
        problems = z.call("problem.get", {"hostids": ids, "output": ["eventid", "name", "severity", "clock"],
                                          "selectTags": ["tag", "value"], "recent": False}) if ids else []
        trend_ids = [i["itemid"] for i in items if KEYS.get(i["key_"]) in ("delay", "connect", "web")]
        trends: dict[str, list] = {}
        since = int(time.time()) - 86400
        for start in range(0, len(trend_ids), 50):
            for r in z.call("trend.get", {"itemids": trend_ids[start:start + 50], "time_from": since,
                                          "output": ["itemid", "clock", "value_avg"]}):
                trends.setdefault(r["itemid"], []).append([int(r["clock"]) * 1000 + 1_800_000,
                                                           round(1000 * float(r["value_avg"]), 1)])
        proxies = {p["name"]: p for p in z.call("proxy.get", {"output": ["name", "state", "lastaccess"]})}
        return hosts, items, problems, trends, proxies

    hosts, items, problems, trends, proxies = data.cache.get(f"paths:{code}", load, ([], [], [], {}, {}), ttl=60)
    now = time.time()
    by_host: dict[str, dict] = {}
    for item in items:
        kind = KEYS.get(item["key_"])
        if kind and int(item.get("lastclock") or 0):
            by_host.setdefault(item["hostid"], {})[kind] = {
                "value": item.get("lastvalue"), "clock": int(item["lastclock"]), "itemid": item["itemid"],
                "broken": str(item.get("state")) == "1", "error": item.get("error") or ""}
    problem_index: dict[tuple[str, str], list[dict]] = {}
    for p in problems:
        tags = {t["tag"]: t["value"] for t in p.get("tags", [])}
        problem_index.setdefault((tags.get("collector", ""), tags.get("path_target", "")), []).append(
            {"name": p["name"], "severity": int(p["severity"]),
             "since": datetime.fromtimestamp(int(p["clock"]), data.tz()).isoformat(timespec="seconds")})
    collectors: dict[str, dict] = {}
    for h in hosts:
        tags = {t["tag"]: t["value"] for t in h.get("tags", [])}
        macros = {m["macro"]: m["value"] for m in h.get("macros", [])}
        collector, name = tags.get("collector", ""), tags.get("path_target", h["name"])
        values = by_host.get(h["hostid"], {})
        iface = next((i for i in h.get("interfaces", []) if str(i.get("main")) == "1"), {})
        mine = problem_index.get((collector, name), [])
        checks = [c for c, template in path_tests.CHECK_TEMPLATES.items()
                  if template in {t["host"] for t in h.get("parentTemplates", [])}]

        def number(kind, scale=1000.0):
            v = values.get(kind)
            try:
                return round(float(v["value"]) * scale, 1) if v and v["value"] not in (None, "") else None
            except ValueError:
                return None
        series_kind = "delay" if "delay" in values else "connect" if "connect" in values else "web"
        series_item = (values.get(series_kind) or {}).get("itemid")
        status = values.get("status")
        target = {
            "name": name, "segment": tags.get("segment", "application"), "checks": checks,
            "address": iface.get("ip") if str(iface.get("useip")) == "1" else iface.get("dns", ""),
            "url": macros.get("{$PATH.URL}", ""),
            "delayMs": number("delay"), "lossPct": number("loss", 1.0), "connectMs": number("connect"),
            "webMs": number("web"), "webStatus": int(float(status["value"])) if status and status["value"] else None,
            "webError": (values.get("error") or {}).get("value") or "",
            "limits": {"delayMs": _ms(macros.get("{$PATH.RTT.WARN}")), "lossPct": _float(macros.get("{$PATH.LOSS.WARN}")),
                       "webMs": _ms(macros.get("{$PATH.HTTP.WARN}"))},
            "broken": [f"{k}: {v['error']}" for k, v in values.items() if v["broken"]],
            "problems": sorted(mine, key=lambda p: -p["severity"]),
            "historyKind": series_kind, "history": sorted(trends.get(series_item, [])) if series_item else [],
            "lastSeen": datetime.fromtimestamp(max(v["clock"] for v in values.values()), data.tz()).isoformat(
                timespec="seconds") if values else None,
        }
        target["state"] = _target_state(mine, values, now)
        proxy = proxies.get(collector, {})
        entry = collectors.setdefault(collector, {
            "name": collector, "online": str(proxy.get("state")) == "2",
            "lastSeen": datetime.fromtimestamp(int(proxy["lastaccess"]), data.tz()).isoformat(timespec="seconds")
            if proxy.get("lastaccess") and int(proxy["lastaccess"]) else None, "targets": []})
        entry["targets"].append(target)
    out = []
    for c in sorted(collectors.values(), key=lambda c: c["name"]):
        c["targets"].sort(key=lambda t: (path_tests.SEGMENTS.index(t["segment"]) if t["segment"] in path_tests.SEGMENTS
                                         else 9, t["name"]))
        c["segments"] = segment_states(c["targets"])
        c["verdict"] = verdict(c["targets"], c["online"])
        out.append(c)
    configured, errors = (path_tests.tenant_targets(tenant) if tenant else ([], []))
    return {"collectors": out, "hasCollector": bool(tenant and tenant.proxy_group),
            "targets": [t.as_dict() for t in configured], "errors": errors,
            "freshness": data.cache.loaded(f"paths:{code}").isoformat(timespec="seconds")
            if data.cache.loaded(f"paths:{code}") else None}


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ms(value) -> float | None:
    seconds = _float(value)
    return None if seconds is None else round(seconds * 1000, 1)


# =============================================================================== capacity
RANK = {"over": 0, "at_risk": 1, "ok": 2, "too_little_data": 3, "no_capacity": 4, "no_data": 5, "not_monitored": 6}


def _link(x: dict) -> dict:
    glpi = settings.glpi_public_url.rstrip("/") or settings.public_url("glpi")
    return {"customer": x["customer"], "customerName": x.get("customer_name", ""), "device": x["device"],
            "site": x.get("site", ""), "interface": x["interface"], "description": x.get("description", ""),
            "circuit": x.get("circuit") or None, "provider": x.get("provider") or None,
            "capacityBps": x.get("capacity_bps") or None, "capacityFrom": x.get("capacity_from") or None,
            "direction": x.get("direction") or None, "status": x["status"], "busyBps": x.get("busy_bps"),
            "peakBps": x.get("peak_bps"), "utilisation": x.get("utilisation"), "slopeBpsPerDay": x.get("slope_bps_per_day"),
            "daysToWarn": x.get("days_to_warn"), "days": x.get("days", 0),
            "history": [[int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=data.tz()).timestamp() * 1000) + 43_200_000, v]
                        for d, v in x.get("history") or []],
            "ticketId": x.get("ticket_id"),
            "ticketLink": f"{glpi}/front/ticket.form.php?id={int(x['ticket_id'])}" if x.get("ticket_id") and glpi else None}


def capacity(code: str = "", only_trouble: bool = False) -> dict:
    result = data.cache.get("capacity", capacity_forecast.load, {"links": []}, ttl=300)
    links = [x for x in result.get("links", []) if (not code or x.get("customer") == code)
             and (not only_trouble or x.get("status") in ("over", "at_risk"))]
    links.sort(key=lambda x: (RANK.get(x["status"], 9), x["days_to_warn"] if x.get("days_to_warn") is not None else 1e9,
                              -(x.get("utilisation") or 0)))
    return {"generated": result.get("generated"), "settings": result.get("settings") or {},
            "links": [_link(x) for x in links],
            "errors": [e for e in result.get("errors", []) if not code or e.startswith(f"{code}:")]}
