"""Per-device and per-customer detail for the MSP Console: Zabbix charts and their values, interfaces
with their cables and circuits from Nautobot, the network map, configuration backups from Forgejo and
Golden Config's rule-by-rule results. Read-only, cached like the other loaders."""

from __future__ import annotations

import logging
import re
import time

from urllib.parse import quote

from .. import clients
from ..settings import settings
from . import data

log = logging.getLogger("msp_glue.console.detail")

RANGES = {  # span, history or hourly trends, and the step the front end may assume
    "1h": (3600, "history"), "6h": (6 * 3600, "history"), "24h": (86400, "history"),
    "7d": (7 * 86400, "trends"), "30d": (30 * 86400, "trends"),
}
MAX_POINTS = 1500   # per series; longer raw histories are averaged into buckets

ROLE = {"router": "router", "sd-wan-edge": "router", "firewall": "firewall", "switch": "switch",
        "server": "server", "hypervisor": "server", "virtual-machine": "server", "access-point": "wireless",
        "wireless": "wireless"}


def role_of(name: str) -> str:
    """Nautobot role -> one of the console's five device roles."""
    key = (name or "").strip().lower()
    if key in ROLE:
        return ROLE[key]
    for word, role in (("router", "router"), ("firewall", "firewall"), ("switch", "switch"), ("server", "server"),
                       ("access", "wireless"), ("wifi", "wireless"), ("wireless", "wireless")):
        if word in key:
            return role
    return "switch"


# ============================================================================ Zabbix items
def items(hostid: str) -> list[dict]:
    def load():
        return clients.zabbix().call("item.get", {
            "hostids": [hostid], "filter": {"status": 0},
            "output": ["itemid", "key_", "name", "units", "value_type", "lastvalue", "lastclock", "delay", "state"],
            "selectTags": ["tag", "value"],
        })
    return data.cache.get(f"items:{hostid}", load, [], ttl=60)


def _tags(item: dict) -> dict:
    return {t["tag"]: t["value"] for t in item.get("tags") or []}


_FS = re.compile(r"^vfs\.fs\.(?:dependent\.)?size\[\"?([^,\"]+)\"?,\s*pused\]$")


def _interface_of(item: dict) -> str:
    tag = _tags(item).get("interface")
    if tag:
        return tag
    match = re.match(r"^Interface ([^():]+?)(?:\(.*\))?:", item.get("name") or "")
    return match.group(1).strip() if match else ""


def chart_items(hostid: str) -> dict[str, dict]:
    """Chart id -> {title, units, interface, series: [(key, name, item)]} for the items the host has."""
    found = items(hostid)
    numeric = [i for i in found if str(i.get("value_type")) in ("0", "3")]
    out: dict[str, dict] = {}

    def add(chart_id, title, units, series, interface=None):
        out[chart_id] = {"title": title, "units": units, "interface": interface, "series": series}

    by_key = {i["key_"]: i for i in numeric}
    if "icmppingsec" in by_key:
        add("icmp_rtt", "Response time", "ms", [("icmp_rtt", "Response time", by_key["icmppingsec"])])
    if "icmppingloss" in by_key:
        add("icmp_loss", "Packet loss", "%", [("icmp_loss", "Packet loss", by_key["icmppingloss"])])
    cpu = by_key.get("system.cpu.util") or next(
        (i for i in numeric if i["key_"].startswith("system.cpu.util[") and not i["key_"].startswith("system.cpu.util[,")
         and i.get("units") == "%"), None)
    if cpu:
        add("cpu", "CPU", "%", [("cpu", "CPU", cpu)])
    memory = by_key.get("vm.memory.util") or by_key.get("vm.memory.utilization") or next(
        (i for i in numeric if i["key_"].startswith("vm.memory.util") and i.get("units") == "%"), None)
    if memory:
        add("memory", "Memory used", "%", [("memory", "Memory used", memory)])
    for i in sorted(numeric, key=lambda x: x["key_"]):
        match = _FS.match(i["key_"])
        if match:
            fs = match.group(1)
            add(f"fs:{fs}", f"Disk used, {fs}", "%", [(f"fs:{fs}", f"Disk used, {fs}", i)])
    traffic: dict[str, dict] = {}
    for i in numeric:
        key = i["key_"]
        direction = "in" if key.startswith("net.if.in[") else "out" if key.startswith("net.if.out[") else None
        if not direction or "error" in key or "discard" in key:
            continue
        name = _interface_of(i)
        if name and name != "lo":
            traffic.setdefault(name, {})[direction] = i
    for name in sorted(traffic):
        pair = traffic[name]
        series = [(d, d.title(), pair[d]) for d in ("in", "out") if d in pair]
        add(f"if:{name}", f"Traffic, {name}", "bps", series, interface=name)
    return out


def charts(device: dict) -> list[dict]:
    host = data.hosts().get(device["id"])
    if not host:
        return []
    return [{"id": cid, "title": c["title"], "units": c["units"], **({"interface": c["interface"]} if c["interface"] else {})}
            for cid, c in chart_items(host["hostid"]).items()]


def metrics(device: dict, span_name: str, wanted: list[str]) -> dict:
    """Values of the wanted charts: raw history up to 24 hours, hourly trends beyond."""
    span, source = RANGES.get(span_name, RANGES["6h"])
    host = data.hosts().get(device["id"])
    if not host:
        return {"range": span_name, "source": source, "step": 3600 if source == "trends" else 60, "charts": []}
    available = chart_items(host["hostid"])
    till = int(time.time())
    since = till - span
    z = clients.zabbix()
    out, steps = [], []
    for chart_id in wanted:
        chart = available.get(chart_id)
        if not chart:
            continue
        series = []
        for key, name, item in chart["series"]:
            scale = 1000.0 if item["key_"] == "icmppingsec" else 1.0
            step = 3600 if source == "trends" else _delay_seconds(item.get("delay"))
            steps.append(step)
            if source == "trends":
                rows = z.call("trend.get", {"itemids": [item["itemid"]], "time_from": since, "time_till": till,
                                            "output": ["clock", "value_avg"]})
                # an hour's average, drawn in the middle of its hour
                points = [(int(r["clock"]) + 1800, float(r["value_avg"]) * scale) for r in rows]
            else:
                rows = z.call("history.get", {"itemids": [item["itemid"]], "history": int(item["value_type"]),
                                              "time_from": since, "time_till": till, "sortfield": "clock",
                                              "sortorder": "ASC", "output": ["clock", "value"], "limit": 20000})
                points = [(int(r["clock"]), float(r["value"]) * scale) for r in rows]
            series.append({"key": key, "name": name, "points": _with_gaps(_thin(points), step, since, till)})
        info = {"id": chart_id, "title": chart["title"], "units": chart["units"]}
        if chart["interface"]:
            info["interface"] = chart["interface"]
        out.append({**info, "series": series})
    return {"range": span_name, "source": source, "step": max(steps) if steps else 60, "charts": out}


def _delay_seconds(delay) -> int:
    match = re.match(r"^(\d+)([smhdw]?)$", str(delay or "").strip())
    if not match:
        return 60
    return int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]


def _thin(points: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """Average a long raw history into at most MAX_POINTS buckets."""
    if len(points) <= MAX_POINTS:
        return points
    first, last = points[0][0], points[-1][0]
    width = max(1, (last - first) // MAX_POINTS + 1)
    buckets: dict[int, list[float]] = {}
    for clock, value in points:
        buckets.setdefault((clock - first) // width, []).append(value)
    return [(first + b * width + width // 2, sum(v) / len(v)) for b, v in sorted(buckets.items())]


def _with_gaps(points: list[tuple[int, float]], step: int, since: int, till: int) -> list[list]:
    """[epoch ms, value] pairs, with a null where Zabbix has no value for longer than three steps, so the
    chart shows the gap (a device that did not answer) instead of a line across it."""
    out: list[list] = []
    previous = None
    gap = 3 * max(step, 60)
    for clock, value in points:
        if previous is not None and clock - previous > gap:
            out.append([(previous + step) * 1000, None])
        out.append([clock * 1000, round(value, 4)])
        previous = clock
    if previous is not None and till - previous > gap:
        out.append([(previous + step) * 1000, None])
    elif previous is None:
        out.append([since * 1000, None])
    return out


# =========================================================================== interfaces
INTERFACES_QUERY = """
query ($ids: [String]) {
  interfaces(device_id: $ids) {
    id name description enabled mgmt_only type speed
    device { id name }
    cable { id status { name } }
    cable_peer_interface { id name device { id name } }
    cable_peer_circuit_termination {
      id term_side
      circuit { id cid commit_rate status { name } provider { name } circuit_type { name } }
    }
  }
}
"""


def nautobot_interfaces(device_ids: list[str]) -> list[dict]:
    key = "interfaces:" + ",".join(sorted(device_ids))

    def load():
        if not device_ids:
            return []
        return clients.nautobot().graphql(INTERFACES_QUERY, {"ids": device_ids})["interfaces"]
    return data.cache.get(key, load, [])


def _status_value(item: dict) -> str | None:
    key, value = item["key_"], str(item.get("lastvalue") or "").strip().lower()
    if "operstate" in key:
        return "up" if value in ("6", "up") else "down" if value in ("2", "3", "down", "lowerlayerdown") else None
    if "ifoperstatus" in key.lower() or key.startswith("net.if.status["):
        return "up" if value == "1" else "down" if value in ("2", "7") else None
    return None


def _zabbix_interfaces(hostid: str | None) -> dict[str, dict]:
    """Interface name -> latest status, traffic and errors from Zabbix."""
    if not hostid:
        return {}
    out: dict[str, dict] = {}
    fresh = time.time() - 900
    for i in items(hostid):
        name = _interface_of(i)
        if not name:
            continue
        entry = out.setdefault(name, {"status": None, "in": None, "out": None, "errors": None})
        recent = int(i.get("lastclock") or 0) > fresh
        key = i["key_"]
        if "error" in key and recent:
            entry["errors"] = (entry["errors"] or 0) + 60 * float(i.get("lastvalue") or 0)
        elif key.startswith("net.if.in[") and "discard" not in key and recent:
            entry["in"] = float(i.get("lastvalue") or 0)
        elif key.startswith("net.if.out[") and "discard" not in key and recent:
            entry["out"] = float(i.get("lastvalue") or 0)
        else:
            state = _status_value(i)
            if state and recent:
                entry["status"] = state
    return out


def interfaces(device: dict) -> list[dict]:
    host = data.hosts().get(device["id"])
    zbx = _zabbix_interfaces(host["hostid"] if host else None)
    host_down = bool(host) and host["state"] == "down"
    out = []
    for i in nautobot_interfaces([device["id"]]):
        name = i["name"]
        live = zbx.pop(name, {})
        peer = i.get("cable_peer_interface")
        term = i.get("cable_peer_circuit_termination")
        circuit = (term or {}).get("circuit") or {}
        if not i.get("enabled", True):
            status = "admin-down"
        elif host_down:
            status = "down"
        else:
            # Zabbix's own reading when it has one; otherwise the interface counts as up while the device answers.
            status = live.get("status") or ("up" if host and host["state"] == "up" else "down")
        out.append({
            "name": name, "description": i.get("description") or "", "status": status,
            "speed": int(i.get("speed") or circuit.get("commit_rate") or 0) * 1000,
            "inBps": None if host_down else live.get("in"), "outBps": None if host_down else live.get("out"),
            "errorsPerMin": None if host_down else live.get("errors"),
            "peer": ({"device": peer["device"]["name"], "interface": peer["name"]} if peer else
                     {"device": (circuit.get("provider") or {}).get("name") or "Provider", "interface": circuit.get("cid", "")}
                     if circuit else None),
            "circuit": circuit.get("cid"),
        })
    # Interfaces Zabbix sees that Nautobot does not record (discovered by SNMP or the agent)
    for name, live in sorted(zbx.items()):
        if live.get("in") is None and live.get("out") is None and not live.get("status"):
            continue
        out.append({"name": name, "description": "Seen by Zabbix only", "status": "down" if host_down else live.get("status") or "up",
                    "speed": 0, "inBps": live.get("in"), "outBps": live.get("out"), "errorsPerMin": live.get("errors"),
                    "peer": None, "circuit": None})
    out.sort(key=lambda x: (x["status"] == "admin-down", _natural(x["name"])))
    return out


def _natural(text: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


# ============================================================================ topology
def topology(code: str) -> dict:
    mine = [d for d in data.devices() if d["customer"] == code]
    hosts = data.hosts()
    names = {d["id"]: d["name"] for d in mine}
    nodes = []
    for d in mine:
        host = hosts.get(d["id"])
        if d["status"] in ("Planned", "Staged", "Inventory"):
            state = "planned"
        elif host:
            state = host["state"]
        else:
            state = "unknown" if d["monitoring"] else "unmonitored"
        nodes.append({"id": d["id"], "name": d["name"], "role": role_of(d["role"]), "site": d["site_id"] or None,
                      "state": state, "maintenance": bool(host and host["maintenance"]), "ip": d["ip"]})
    states = {n["id"]: n["state"] for n in nodes}
    links, seen = [], set()
    external = None
    for i in nautobot_interfaces(list(names)):
        cable = i.get("cable") or {}
        if not cable.get("id") or cable["id"] in seen:
            continue
        a = i["device"]["id"]
        peer = i.get("cable_peer_interface")
        term = i.get("cable_peer_circuit_termination")
        if peer and peer["device"]["id"] in names:
            seen.add(cable["id"])
            b = peer["device"]["id"]
            links.append({"id": cable["id"], "a": a, "aInterface": i["name"], "b": b, "bInterface": peer["name"],
                          "speed": int(i.get("speed") or 0) * 1000, "state": _link_state(states[a], states[b]),
                          "kind": "cable", "circuit": None})
        elif term:
            seen.add(cable["id"])
            circuit = term.get("circuit") or {}
            external = external or f"provider-{code}"
            links.append({"id": cable["id"], "a": a, "aInterface": i["name"], "b": external,
                          "bInterface": circuit.get("cid", ""), "speed": int(circuit.get("commit_rate") or i.get("speed") or 0) * 1000,
                          "state": _link_state(states[a], "up"), "kind": "circuit", "circuit": circuit.get("cid")})
    if external:
        nodes.insert(0, {"id": external, "name": "IOH network", "role": "network", "site": None, "state": "external",
                         "maintenance": False, "ip": ""})
    return {"sites": data.sites(code), "nodes": nodes, "links": links}


def _link_state(a: str, b: str) -> str:
    if "down" in (a, b):
        return "down"
    if a == "up" and b in ("up", "external"):
        return "up"
    return "unknown"


# ============================================================================== backups
def backup_history(device: dict, code: str) -> list[dict]:
    """The last configuration backups of a device: commits that touched its file in the customer's backup
    repository in Forgejo."""
    def load():
        prefix = settings.mappings.get("golden_config", {}).get("backup_repo_prefix", "backups-")
        repo = f"{settings.forgejo_org}/{prefix}{code}"
        fj = clients.forgejo()
        tree = fj.request("GET", f"repos/{repo}/git/trees/main?recursive=true", ok404=True) or {}
        path = next((t["path"] for t in tree.get("tree", []) if t["path"].endswith(f"/{device['name']}.cfg")
                     or t["path"] == f"{device['name']}.cfg"), None)
        if not path:
            return []
        commits = fj.request("GET", f"repos/{repo}/commits?path={quote(path)}&limit=10&stat=true&verification=false"
                                    f"&files=false", ok404=True) or []
        web = settings.public_url("forgejo")
        out = []
        for c in commits:
            stats = c.get("stats") or {}
            message = (c.get("commit") or {}).get("message", "").strip().splitlines()
            out.append({"at": data.iso_time(c.get("created") or (c.get("commit") or {}).get("author", {}).get("date")),
                        "commit": c["sha"][:7], "changedLines": int(stats.get("total") or 0),
                        "message": message[0] if message else "",
                        "link": f"{web}/{repo}/commit/{c['sha']}"})
        return out
    return data.cache.get(f"backups:{device['id']}", load, [], ttl=300)


def compliance_rules(device: dict) -> list[dict]:
    def load():
        rows = clients.nautobot().list("plugins/golden-config/config-compliance/", device=device["id"], depth=1)
        out = []
        for r in rows:
            out.append({"rule": (r.get("rule") or {}).get("display") or "rule", "compliant": bool(r.get("compliance")),
                        "missing": [mask(x) for x in (r.get("missing") or "").splitlines() if x.strip()],
                        "extra": [mask(x) for x in (r.get("extra") or "").splitlines() if x.strip()]})
        out.sort(key=lambda r: (r["compliant"], r["rule"]))
        return out
    return data.cache.get(f"rules:{device['id']}", load, [])


# Words after which a configuration line carries a secret, and words that may stand between such a word and
# the secret itself (an encryption type, a hash or cipher name, a key number).
_SECRET_WORDS = {"password", "passwd", "secret", "community", "key", "key-string", "pre-shared-key", "psk",
                 "psksecret", "auth-key", "authentication-key", "auth", "priv", "private", "token", "passphrase",
                 "md5", "encrypted-password", "shared-secret", "wpa-psk"}
_BETWEEN = {"md5", "sha", "sha1", "sha256", "sha384", "sha512", "aes", "aes128", "aes192", "aes256", "des", "3des",
            "encrypted", "hidden", "cleartext", "plaintext", "unencrypted", "ascii", "ascii-text", "hex",
            "hexadecimal", "enc"}


def _secret_word(word: str) -> bool:
    return word in _SECRET_WORDS or word.endswith(("-password", "-secret", "-key"))


def mask(line: str) -> str:
    """Configuration lines can carry secrets: keep the keywords, hide the value that follows them
    ("secret 5 <hidden>", "community <hidden> RO", "password=<hidden>")."""
    parts = re.split(r"(\s+)", line)
    words = [i for i, p in enumerate(parts) if p and not p.isspace()]
    waiting = False
    for i in words:
        part = parts[i]
        word = part.strip("\"'").lower()
        if "=" in part and not waiting:
            left, _sep, _right = part.partition("=")
            if _secret_word(left.strip("\"'").lower()):
                parts[i] = f"{left}=<hidden>"
            continue
        if waiting:
            last = i == words[-1]
            if word in _BETWEEN or word in _SECRET_WORDS or (word.isdigit() and len(word) <= 3 and not last):
                continue
            parts[i] = "<hidden>"
            waiting = False
        elif _secret_word(word):
            waiting = True
    return "".join(parts)


def zabbix_host_link(hostid: str) -> str:
    return f"{settings.public_url('zabbix')}/zabbix.php?action=latest.view&hostids%5B%5D={hostid}&filter_set=1"
