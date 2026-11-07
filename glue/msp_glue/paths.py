"""Path tests from each customer's site collectors (kit 1.6.0).

Every site collector (a Zabbix proxy in the customer's proxy group) checks a short list of targets: IOH's edge
router at the site, public reference points beyond IOH, and the applications the customer uses. msp-glue keeps
one Zabbix host per collector and target, monitored by that collector, with the templates of
zabbix/templates/msp_path_tests.yaml. The MSP Console compares the three segments to say on which side of the
path a slowdown is.

Targets for every customer are in mappings.yaml (path_tests.defaults). A customer adds its own in the Nautobot
tenant field msp_path_targets, a JSON list:

    [{"name": "IOH edge router", "address": "10.20.30.1", "segment": "ioh"},
     {"name": "Core banking", "url": "https://core.example.co.id/health", "segment": "application"},
     {"name": "File server", "address": "10.9.9.10", "checks": ["tcp"], "port": 445, "collector": "acme-jkt-01"}]

or {"defaults": false, "targets": [...]} to leave out the defaults. "collector" limits a target to one collector.

The hosts carry the tag managed_by=msp-glue-paths, so they never open GLPI incidents, never reach the NOC chat and
never count as downtime: a path test says where a problem is, while the managed devices say whether it is an outage.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import clients
from .clients.zabbix import Zabbix
from .common import Tenant, zabbix_safe
from .devices import SyncReport
from .settings import settings

log = logging.getLogger("msp_glue.paths")

MANAGED_VALUE = "msp-glue-paths"
CHECK_TEMPLATES = {"icmp": "MSP path test ICMP", "tcp": "MSP path test TCP", "https": "MSP path test HTTPS"}
SEGMENTS = ("ioh", "internet", "application")
_SEGMENT_WORDS = {"ioh": "ioh", "edge": "ioh", "access": "ioh", "internet": "internet", "public": "internet",
                  "application": "application", "app": "application", "apps": "application", "saas": "application"}
HOST_GROUP = "path tests"
TEMPLATE_FILE_NAME = "msp_path_tests.yaml"
_TEMPLATE_PLACES = (Path("/app/seed/zabbix-templates") / TEMPLATE_FILE_NAME,
                    Path(__file__).resolve().parents[2] / "zabbix" / "templates" / TEMPLATE_FILE_NAME)
MAX_TARGETS = 20


@dataclass
class Target:
    name: str
    segment: str
    address: str = ""
    url: str = ""
    port: int = 443
    checks: list[str] = field(default_factory=list)
    rtt_ms: float = 150
    loss_percent: float = 5
    http_ms: float = 2000
    collector: str = ""

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")[:40] or "target"

    def as_dict(self) -> dict:
        return {"name": self.name, "segment": self.segment, "address": self.address, "url": self.url,
                "port": self.port, "checks": self.checks, "rtt_ms": self.rtt_ms, "loss_percent": self.loss_percent,
                "http_ms": self.http_ms, "collector": self.collector}


# =============================================================================== targets
def _config() -> dict:
    return settings.mappings.get("path_tests") or {}


def _number(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


def parse_target(raw: dict, limits: dict | None = None) -> Target:
    """One target from mappings.yaml or the tenant field. Raises ValueError with a message for the engineer."""
    if not isinstance(raw, dict):
        raise ValueError(f"each target must be an object with a name, not {raw!r}")
    limits = limits or {}
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError(f"a target has no name: {json.dumps(raw)[:120]}")
    segment = _SEGMENT_WORDS.get(str(raw.get("segment") or "application").strip().lower())
    if not segment:
        raise ValueError(f"{name}: segment must be ioh, internet or application")
    url = str(raw.get("url") or "").strip()
    address = str(raw.get("address") or "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"{name}: url must start with https:// or http:// and name a host")
        address = address or parsed.hostname
    if not address:
        raise ValueError(f"{name}: give an address (IP or name) or a url")
    if not re.fullmatch(r"[A-Za-z0-9.:-]+", address):
        raise ValueError(f"{name}: '{address}' is not an IP address or host name")
    checks = raw.get("checks")
    if checks is None:
        checks = ["https"] if url else ["icmp"]
    if isinstance(checks, str):
        checks = [checks]
    checks = [str(c).strip().lower() for c in checks]
    unknown = [c for c in checks if c not in CHECK_TEMPLATES]
    if unknown or not checks:
        raise ValueError(f"{name}: checks must be a list of icmp, tcp and https")
    if "https" in checks and not url:
        raise ValueError(f"{name}: the https check needs a url")
    try:
        port = int(raw.get("port") or (urlparse(url).port if url else None) or 443)
    except ValueError as exc:
        raise ValueError(f"{name}: port must be a number") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name}: port must be between 1 and 65535")
    return Target(
        name=name[:60], segment=segment, address=address, url=url, port=port, checks=list(dict.fromkeys(checks)),
        rtt_ms=_number(raw.get("rtt_ms", limits.get("rtt_ms")), 150, 1, 10000),
        loss_percent=_number(raw.get("loss_percent", limits.get("loss_percent")), 5, 0, 99),
        http_ms=_number(raw.get("http_ms", limits.get("http_ms")), 2000, 50, 60000),
        collector=str(raw.get("collector") or "").strip(),
    )


def tenant_targets(tenant: Tenant) -> tuple[list[Target], list[str]]:
    """The customer's targets (defaults plus its own) and messages about entries that could not be used."""
    cfg = _config()
    limits = cfg.get("limits") or {}
    errors: list[str] = []
    own = tenant.custom_fields.get("msp_path_targets")
    if isinstance(own, str):
        try:
            own = json.loads(own) if own.strip() else None
        except json.JSONDecodeError as exc:
            errors.append(f"msp_path_targets is not valid JSON: {exc}")
            own = None
    use_defaults, own_list = True, []
    if isinstance(own, dict):
        use_defaults = own.get("defaults", True) is not False
        own_list = own.get("targets") or []
    elif isinstance(own, list):
        own_list = own
    elif own is not None:
        errors.append("msp_path_targets must be a list of targets, or an object with defaults and targets")
    raw = (list(cfg.get("defaults") or []) if use_defaults else []) + list(own_list)
    targets: dict[str, Target] = {}
    for entry in raw:
        try:
            target = parse_target(entry, limits)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        targets[target.name.lower()] = target     # the customer's own entry replaces a default of the same name
    found = list(targets.values())
    if len(found) > MAX_TARGETS:
        errors.append(f"{len(found)} targets: only the first {MAX_TARGETS} are tested")
        found = found[:MAX_TARGETS]
    return found, errors


# ============================================================================= templates
def template_file() -> Path | None:
    return next((p for p in _TEMPLATE_PLACES if p.is_file()), None)


def template_version(text: str) -> str:
    data = yaml.safe_load(text) or {}
    templates = (data.get("zabbix_export") or {}).get("templates") or []
    return str(((templates[0] if templates else {}).get("vendor") or {}).get("version") or "")


def ensure_templates(z: Zabbix) -> bool:
    """Import the path test templates when they are missing or older than this kit. True when they exist."""
    path = template_file()
    if not path:
        log.warning("Path tests: %s not found; mount zabbix/templates at /app/seed/zabbix-templates", TEMPLATE_FILE_NAME)
        return bool(z.template_ids(list(CHECK_TEMPLATES.values())))
    text = path.read_text(encoding="utf-8")
    wanted = template_version(text)
    found = z.call("template.get", {"filter": {"host": list(CHECK_TEMPLATES.values())},
                                    "output": ["host", "vendor_version"]})
    if len(found) == len(CHECK_TEMPLATES) and all(t.get("vendor_version") == wanted for t in found):
        return True
    log.info("Zabbix: importing the path test templates (version %s)", wanted)
    if settings.dry_run:
        return len(found) == len(CHECK_TEMPLATES)
    rule = {"createMissing": True, "updateExisting": True}
    z.call("configuration.import", {"format": "yaml", "source": text, "rules": {
        "template_groups": rule, "templates": rule, "valueMaps": {**rule, "deleteMissing": True},
        "items": {**rule, "deleteMissing": True}, "triggers": {**rule, "deleteMissing": True},
        "templateLinkage": {"createMissing": True},
    }})
    return True


# ============================================================================ collectors
def collectors(z: Zabbix, tenant: Tenant) -> list[dict]:
    """The customer's site collectors: proxies in its own proxy group (none when it uses the central proxies)."""
    if not tenant.proxy_group:
        return []
    group_id = z.proxy_group_id(tenant.proxy_group)
    if not group_id:
        return []
    found = z.call("proxy.get", {"output": ["proxyid", "name", "state", "lastaccess"],
                                 "filter": {"proxy_groupid": [group_id]}})
    return sorted(found, key=lambda p: p["name"])


# ================================================================================= hosts
def host_name(tenant: Tenant, collector: str, target: Target) -> str:
    return zabbix_safe(f"{tenant.code}-path-{collector}-{target.slug}")


def _interface(address: str) -> dict:
    try:
        ipaddress.ip_address(address)
        return {"type": 1, "main": 1, "useip": 1, "ip": address, "dns": "", "port": "10050"}
    except ValueError:
        return {"type": 1, "main": 1, "useip": 0, "ip": "", "dns": address, "port": "10050"}


def host_macros(target: Target, collector: str) -> list[dict]:
    cfg = _config()
    macros = {
        "{$PATH.NAME}": target.name, "{$PATH.COLLECTOR}": collector, "{$PATH.SEGMENT}": target.segment,
        "{$PATH.RTT.WARN}": f"{target.rtt_ms / 1000:g}", "{$PATH.LOSS.WARN}": f"{target.loss_percent:g}",
        "{$PATH.HTTP.WARN}": f"{target.http_ms / 1000:g}", "{$PATH.PORT}": str(target.port),
        "{$PATH.URL}": target.url, "{$PATH.INTERVAL}": str(cfg.get("interval") or "1m"),
        "{$PATH.HTTP.INTERVAL}": str(cfg.get("http_interval") or "5m"),
    }
    return [{"macro": k, "value": v} for k, v in macros.items()]


def host_tags(tenant: Tenant, collector: str, target: Target) -> list[dict]:
    # "path_target", not "target": Zabbix's own templates use the tag target for the kind of template.
    tags = {"managed_by": MANAGED_VALUE, "tenant": tenant.code, "tenant_name": tenant.name, "collector": collector,
            "segment": target.segment, "path_target": target.name}
    return [{"tag": k, "value": v} for k, v in tags.items()]


def _tag(host: dict, name: str) -> str:
    return next((t["value"] for t in host.get("tags", []) if t["tag"] == name), "")


def managed_path_hosts(z: Zabbix, tenant_code: str = "") -> list[dict]:
    tags = [{"tag": "managed_by", "value": MANAGED_VALUE, "operator": 1}]
    if tenant_code:
        tags.append({"tag": "tenant", "value": tenant_code, "operator": 1})
    return z.call("host.get", {
        "output": ["hostid", "host", "name", "status", "proxyid"], "tags": tags, "evaltype": 0,
        "selectTags": ["tag", "value"], "selectParentTemplates": ["templateid", "host"],
        "selectInterfaces": ["interfaceid", "type", "ip", "dns", "useip", "port", "main"],
    })


def sync_tenant(tenant: Tenant, z: Zabbix | None = None) -> SyncReport:
    report = SyncReport()
    if not tenant.valid:
        return report
    z = z or clients.zabbix()
    targets, errors = tenant_targets(tenant)
    report.errors.extend(f"{tenant.code}: {e}" for e in errors)
    found = collectors(z, tenant)
    existing = {h["host"]: h for h in managed_path_hosts(z, tenant.code)}
    wanted: set[str] = set()
    if found and targets:
        if not ensure_templates(z):
            report.errors.append(f"{tenant.code}: the path test templates are missing in Zabbix")
            return report
        templates = z.template_ids(list(CHECK_TEMPLATES.values()))
        group_ids = None
        for proxy in found:
            names_used: set[str] = set()
            for target in targets:
                if target.collector and target.collector != proxy["name"]:
                    continue
                name = host_name(tenant, proxy["name"], target)
                if name in names_used:      # two targets whose names make the same slug
                    name = zabbix_safe(f"{name}-{len(names_used) + 1}")
                names_used.add(name)
                wanted.add(name)
                if group_ids is None:
                    group_ids = _groups(z, tenant)
                try:
                    action = _ensure_host(z, tenant, proxy, target, name, existing.get(name), templates, group_ids)
                    getattr(report, action).append(f"{tenant.code}/{proxy['name']}/{target.name}")
                except Exception as exc:  # noqa: BLE001 - one bad target must not stop the others
                    log.exception("Path test host %s failed", name)
                    report.errors.append(f"{tenant.code}/{proxy['name']}/{target.name}: {exc}")
    for name, host in existing.items():
        if name not in wanted and str(host["status"]) == "0":
            if not settings.dry_run:
                z.host_update({"hostid": host["hostid"], "status": 1,
                               "tags": [t for t in host.get("tags", []) if t["tag"] != "msp_retired"]
                               + [{"tag": "msp_retired", "value": "target or collector removed"}]})
            report.disabled.append(f"{tenant.code}/{_tag(host, 'collector')}/{_tag(host, 'path_target')}")
    if wanted and not settings.dry_run:
        from .tenants import refresh_zabbix_permissions
        refresh_zabbix_permissions(z, tenant)
    return report


def sync_all() -> SyncReport:
    from .tenants import managed_tenants
    report = SyncReport()
    z = clients.zabbix()
    for tenant in managed_tenants():
        try:
            report.merge(sync_tenant(tenant, z))
        except Exception as exc:  # noqa: BLE001 - one customer must not stop the others
            log.exception("Path tests for %s failed", tenant.code)
            report.errors.append(f"{tenant.code}: path tests: {exc}")
    if report.created or report.disabled or report.errors:
        log.info("Path test sync finished: %s", report.summary())
    return report


def _groups(z: Zabbix, tenant: Tenant) -> list[str]:
    return [z.ensure_hostgroup(tenant.zabbix_group), z.ensure_hostgroup(f"{tenant.zabbix_group}/{HOST_GROUP}")]


def _ensure_host(z: Zabbix, tenant: Tenant, proxy: dict, target: Target, name: str, host: dict | None,
                 templates: dict[str, str], group_ids: list[str]) -> str:
    wanted_templates = [templates[CHECK_TEMPLATES[c]] for c in target.checks if CHECK_TEMPLATES[c] in templates]
    params = {
        "host": name,
        "name": f"{target.name} from {proxy['name']} ({tenant.name})"[:128],
        "status": 0, "monitored_by": 1, "proxyid": proxy["proxyid"],
        "description": f"Path test of {tenant.name} ({target.segment}). Managed by msp-glue; change the targets in "
                       "mappings.yaml or the Nautobot tenant field msp_path_targets.",
        "macros": host_macros(target, proxy["name"]),
        "tags": host_tags(tenant, proxy["name"], target),
        "groups": [{"groupid": g} for g in group_ids],
    }
    if settings.dry_run:
        return "created" if host is None else "updated"
    if host is None:
        params.update({"templates": [{"templateid": t} for t in wanted_templates],
                       "interfaces": [_interface(target.address)]})
        z.host_create(params)
        log.info("Zabbix: created path test host %s", name)
        return "created"
    ours = set(templates.values())
    current = [t["templateid"] for t in host.get("parentTemplates", [])]
    params.update({
        "hostid": host["hostid"],
        "templates": [{"templateid": t} for t in dict.fromkeys(wanted_templates + [c for c in current if c not in ours])],
        "templates_clear": [{"templateid": t} for t in current if t in ours and t not in wanted_templates],
    })
    if not params["templates_clear"]:
        params.pop("templates_clear")
    z.host_update(params)
    wanted_iface = _interface(target.address)
    main = next((i for i in host.get("interfaces", []) if str(i["main"]) == "1" and str(i["type"]) == "1"), None)
    if main is None:
        z.call("hostinterface.create", {"hostid": host["hostid"], **wanted_iface})
    elif (main["ip"], main["dns"], str(main["useip"])) != (wanted_iface["ip"], wanted_iface["dns"], str(wanted_iface["useip"])):
        z.interface_update({"interfaceid": main["interfaceid"], "ip": wanted_iface["ip"], "dns": wanted_iface["dns"],
                            "useip": wanted_iface["useip"]})
    return "updated"
