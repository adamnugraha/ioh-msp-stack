"""Device sync: Nautobot devices -> GLPI assets and Zabbix hosts.

Nautobot is the master. msp-glue only touches what it owns:
  * Zabbix: host groups under "Customers/", tags it writes (see MANAGED_TAGS), the main
    interface address, and adds missing templates (it never unlinks templates you add by hand).
  * GLPI: the asset fields listed in _asset_fields(); tickets, contracts and documents are untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import clients
from .clients.glpi import GLPI
from .clients.nautobot import DEVICES_QUERY
from .clients.zabbix import Zabbix
from .common import Tenant, host_ip, zabbix_safe
from .http import APIError, NotFound
from .settings import settings
from .tenants import ensure_tenant, managed_tenants, refresh_zabbix_permissions

log = logging.getLogger("msp_glue.devices")

MANAGED_TAGS = {"managed_by", "tenant", "tenant_name", "device", "site", "role", "platform", "nautobot_id",
                "glpi_entity_id", "glpi_itemtype", "glpi_asset_id", "service_tier"}


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "SyncReport") -> None:
        for name in ("created", "updated", "disabled", "skipped", "errors"):
            getattr(self, name).extend(getattr(other, name))

    def summary(self) -> str:
        return (f"created {len(self.created)}, updated {len(self.updated)}, disabled {len(self.disabled)}, "
                f"skipped {len(self.skipped)}, errors {len(self.errors)}")


# ============================================================================ entry points
def sync_all() -> SyncReport:
    report = SyncReport()
    for tenant in managed_tenants():
        report.merge(sync_tenant(tenant))
    log.info("Device sync finished: %s", report.summary())
    return report


def sync_tenant(tenant: Tenant, only_device_id: str | None = None) -> SyncReport:
    report = SyncReport()
    if not tenant.valid:
        report.skipped.append(f"tenant {tenant.name}: no valid msp_code")
        return report
    if not (tenant.glpi_entity_id and tenant.zabbix_hostgroup_id):
        ensure_tenant(tenant)
        if not (tenant.custom_fields.get("msp_glpi_entity_id") and tenant.custom_fields.get("msp_zabbix_hostgroup_id")):
            report.errors.append(f"tenant {tenant.code}: provisioning incomplete")
            return report
        tenant.glpi_entity_id = tenant.custom_fields["msp_glpi_entity_id"]
        tenant.zabbix_hostgroup_id = tenant.custom_fields["msp_zabbix_hostgroup_id"]

    nb = clients.nautobot()
    all_devices = nb.graphql(DEVICES_QUERY, {"tenant": [tenant.name]})["devices"]
    devices = [d for d in all_devices if d["id"] == only_device_id] if only_device_id else all_devices
    z = clients.zabbix()
    existing = {_tag(h, "nautobot_id"): h for h in z.managed_hosts() if _tag(h, "tenant") == tenant.code}
    ctx = _Context(tenant=tenant, z=z, templates_cache={}, dropdown_cache={})

    with clients.glpi() as g:
        ctx.g = g
        for device in devices:
            try:
                _sync_device(ctx, nb, device, existing.get(device["id"]), report)
            except Exception as exc:  # noqa: BLE001 - one bad device must not stop the others
                log.exception("Sync of device %s failed", device.get("name"))
                report.errors.append(f"{tenant.code}/{device.get('name')}: {exc}")

        if not only_device_id:
            seen = {d["id"] for d in devices}
            for nautobot_id, host in existing.items():
                if nautobot_id not in seen and host["status"] == "0":
                    _retire_host(ctx, host)
                    report.disabled.append(f"{tenant.code}/{host['name']}")

    refresh_zabbix_permissions(z, tenant)
    # Always the whole customer: a webhook for one device must not shrink the Ansible inventory to that device.
    _push_inventory(tenant, all_devices)
    return report


def sync_device(device_id: str) -> SyncReport:
    """Sync a single device (used by the Nautobot webhook)."""
    nb = clients.nautobot()
    try:
        device = nb.request("GET", f"dcim/devices/{device_id}/", params={"depth": 1})
    except NotFound:
        # Nautobot 3.2 reports the deletion of a device that has a primary IP address as an
        # "updated" event, so a device that has disappeared by now is treated as deleted.
        log.info("Device %s no longer exists in Nautobot; retiring it", device_id)
        retire_device(device_id)
        return SyncReport(disabled=[device_id])
    tenant_ref = device.get("tenant")
    if not tenant_ref:
        return SyncReport(skipped=[f"{device.get('name')}: no tenant"])
    for tenant in managed_tenants(nb):
        if tenant.id == tenant_ref["id"]:
            return sync_tenant(tenant, only_device_id=device_id)
    return SyncReport(skipped=[f"{device.get('name')}: tenant is not a managed customer"])


def retire_device(device_id: str) -> None:
    """Called when a device is deleted in Nautobot: disable its Zabbix host, mark the GLPI asset and take it
    out of the customer's Ansible inventory."""
    z = clients.zabbix()
    for host in z.managed_hosts():
        if _tag(host, "nautobot_id") == device_id:
            with clients.glpi() as g:
                _retire_host(_Context(tenant=None, z=z, g=g, templates_cache={}, dropdown_cache={}), host)
            code = _tag(host, "tenant")
            tenant = next((t for t in managed_tenants() if t.code == code), None) if code else None
            if tenant:
                _push_inventory(tenant, clients.nautobot().graphql(DEVICES_QUERY, {"tenant": [tenant.name]})["devices"])


# ============================================================================ internals
@dataclass
class _Context:
    tenant: Tenant | None
    z: Zabbix
    templates_cache: dict
    dropdown_cache: dict
    g: GLPI | None = None


def _tag(host: dict, name: str) -> str:
    for tag in host.get("tags", []):
        if tag["tag"] == name:
            return tag["value"]
    return ""


def _sync_device(ctx: _Context, nb, device: dict, host: dict | None, report: SyncReport) -> None:
    tenant = ctx.tenant
    status = (device.get("status") or {}).get("name", "")
    cf = device.get("custom_fields") or {}
    label = f"{tenant.code}/{device['name']}"

    # ---- GLPI asset (every status in the mapping, including Planned)
    asset_id, itemtype = None, None
    if status in settings.mappings["glpi"]["state_by_status"]:
        asset_id, itemtype = _ensure_asset(ctx, device)

    # ---- Zabbix host (only statuses that should be monitored, and only with an address)
    zstatus = settings.mappings["zabbix"]["status"].get(status)
    hostid = None
    ip = host_ip(device.get("primary_ip4")) or host_ip(device.get("primary_ip6"))
    if zstatus is None or cf.get("msp_monitoring") is False:
        report.skipped.append(f"{label}: not monitored (status {status})")
        if host and host["status"] == "0":
            _retire_host(ctx, host, reason=f"status {status} or monitoring disabled")
            report.disabled.append(label)
    elif not ip:
        report.skipped.append(f"{label}: no primary IP address")
    else:
        hostid, action = _ensure_host(ctx, device, host, ip, zstatus, asset_id, itemtype)
        getattr(report, action).append(label)

    # ---- Device logins for Golden Config: default to the customer's Secrets Group
    patch: dict = {}
    if not device.get("secrets_group"):
        group = nb.first("extras/secrets-groups/", name=f"{tenant.code}-device-login")
        if group:
            patch["secrets_group"] = group["id"]

    # ---- Write cross-references back to Nautobot (only when they changed)
    fields = {"msp_glpi_asset_id": asset_id, "msp_glpi_itemtype": itemtype, "msp_zabbix_hostid": hostid}
    changed = {k: v for k, v in fields.items() if v is not None and cf.get(k) != v}
    if changed:
        patch["custom_fields"] = changed
    if patch and not settings.dry_run:
        nb.patch("dcim/devices/", device["id"], patch)


# ---------------------------------------------------------------------------- GLPI
def _itemtype(device: dict) -> str:
    gm = settings.mappings["glpi"]
    role = ((device.get("role") or {}).get("name") or "").lower()
    return gm["itemtype_by_role"].get(role, gm["default_itemtype"])


def _dropdown(ctx: _Context, itemtype: str, name: str, entity_id: int | None = None) -> int | None:
    if not name:
        return None
    key = (itemtype, name, entity_id)
    if key not in ctx.dropdown_cache:
        match = {"name": name}
        extra = {}
        if entity_id is not None:
            match["entities_id"] = entity_id
            extra = {"is_recursive": 0}
        if settings.dry_run:
            found = ctx.g.find(itemtype, **match)
            ctx.dropdown_cache[key] = int(found[0]["id"]) if found else None
        else:
            ctx.dropdown_cache[key] = ctx.g.ensure(itemtype, match, extra or None)
    return ctx.dropdown_cache[key]


def _asset_fields(ctx: _Context, device: dict, itemtype: str) -> dict:
    gm = settings.mappings["glpi"]
    tenant = ctx.tenant
    entity = int(tenant.glpi_entity_id)
    dtype = device.get("device_type") or {}
    maker = (dtype.get("manufacturer") or {}).get("name") or ((device.get("platform") or {}).get("manufacturer") or {}).get("name")
    status = (device.get("status") or {}).get("name", "")
    model_type = "NetworkEquipmentModel" if itemtype == "NetworkEquipment" else "ComputerModel"
    kind_type = "NetworkEquipmentType" if itemtype == "NetworkEquipment" else "ComputerType"
    model_field = "networkequipmentmodels_id" if itemtype == "NetworkEquipment" else "computermodels_id"
    kind_field = "networkequipmenttypes_id" if itemtype == "NetworkEquipment" else "computertypes_id"
    fields = {
        "name": device["name"],
        "entities_id": entity,
        "serial": device.get("serial") or "",
        "otherserial": device.get("asset_tag") or "",
        "manufacturers_id": _dropdown(ctx, "Manufacturer", maker) or 0,
        model_field: _dropdown(ctx, model_type, dtype.get("model", "")) or 0,
        kind_field: _dropdown(ctx, kind_type, ((device.get("role") or {}).get("name") or "").title()) or 0,
        "locations_id": _dropdown(ctx, "Location", (device.get("location") or {}).get("name", ""), entity) or 0,
        "states_id": _dropdown(ctx, "State", gm["state_by_status"].get(status, "")) or 0,
        "comment": f"Managed by msp-glue. Source of truth: Nautobot device {device['id']}.",
    }
    return fields


def _ensure_asset(ctx: _Context, device: dict) -> tuple[int, str]:
    g = ctx.g
    itemtype = _itemtype(device)
    fields = _asset_fields(ctx, device, itemtype)
    cf = device.get("custom_fields") or {}
    asset_id = None
    if cf.get("msp_glpi_asset_id") and cf.get("msp_glpi_itemtype") == itemtype:
        try:
            current = g.get(itemtype, int(cf["msp_glpi_asset_id"]))
            asset_id = int(current["id"])
        except Exception:  # noqa: BLE001 - deleted in GLPI: recreate below
            current = None
    else:
        current = None
    if current is None:
        found = g.find(itemtype, name=device["name"], entities_id=fields["entities_id"])
        if found:
            current, asset_id = found[0], int(found[0]["id"])
    if current is None:
        if settings.dry_run:
            log.info("Dry run: GLPI would create %s %s in entity %s", itemtype, device["name"], fields["entities_id"])
            return None, itemtype
        asset_id = g.add(itemtype, fields)
        log.info("GLPI: created %s %s in entity %s", itemtype, device["name"], fields["entities_id"])
        return asset_id, itemtype
    changed = {k: v for k, v in fields.items() if str(current.get(k) or "") != str(v or "")}
    if changed and settings.dry_run:
        log.info("Dry run: GLPI would update %s %s (%s)", itemtype, device["name"], ", ".join(sorted(changed)))
    elif changed:
        g.update(itemtype, asset_id, changed)
    return asset_id, itemtype


# --------------------------------------------------------------------------- Zabbix
def _platform_spec(device: dict) -> dict:
    zm = settings.mappings["zabbix"]
    driver = ((device.get("platform") or {}).get("network_driver")) or ""
    return zm["platforms"].get(driver, zm["default_platform"])


def _template_ids(ctx: _Context, names: list[str]) -> list[str]:
    missing = [n for n in names if n not in ctx.templates_cache]
    if missing:
        ctx.templates_cache.update(ctx.z.template_ids(missing))
        for name in missing:
            ctx.templates_cache.setdefault(name, None)
    return [ctx.templates_cache[n] for n in names if ctx.templates_cache.get(n)]


def _interface(device: dict, ip: str, tenant: Tenant) -> dict:
    spec = _platform_spec(device)
    if spec.get("interface") == "agent":
        return {"type": 1, "main": 1, "useip": 1, "ip": ip, "dns": "", "port": "10050"}
    snmp = settings.mappings["zabbix"]["snmp"]
    version = int(tenant.snmp_version or snmp["version"])
    if version == 3:
        details = {"version": 3, "bulk": 1, "contextname": "", **snmp["v3"]}
    else:
        details = {"version": 2, "bulk": 1, "community": snmp["community_macro"]}
    return {"type": 2, "main": 1, "useip": 1, "ip": ip, "dns": "", "port": "161", "details": details}


def _desired_tags(device: dict, tenant: Tenant, asset_id, itemtype) -> list[dict]:
    tags = {
        "managed_by": "msp-glue",
        "tenant": tenant.code,
        "tenant_name": tenant.name,
        "device": device["name"],
        "service_tier": tenant.tier,
        "site": (device.get("location") or {}).get("name", ""),
        "role": (device.get("role") or {}).get("name", ""),
        "platform": (device.get("platform") or {}).get("name", ""),
        "nautobot_id": device["id"],
        "glpi_entity_id": str(tenant.glpi_entity_id or ""),
    }
    if asset_id:
        tags["glpi_asset_id"] = str(asset_id)
        tags["glpi_itemtype"] = itemtype
    return [{"tag": k, "value": v} for k, v in tags.items() if v != ""]


def _ensure_host(ctx: _Context, device: dict, host: dict | None, ip: str, zstatus: int,
                 asset_id, itemtype) -> tuple[str, str]:
    z, tenant = ctx.z, ctx.tenant
    zm = settings.mappings["zabbix"]
    if settings.dry_run:
        # Report only: no host groups, hosts or interfaces are created or changed.
        if host is None:
            log.info("Dry run: Zabbix would create host %s", zabbix_safe(f"{tenant.code}-{device['name']}"))
            return "", "created"
        return host["hostid"], "updated"
    role = (device.get("role") or {}).get("name") or "other"
    group_ids = [z.ensure_hostgroup(tenant.zabbix_group), z.ensure_hostgroup(f"{tenant.zabbix_group}/{role}")]
    spec = _platform_spec(device)
    template_names = list(dict.fromkeys(spec["templates"] + zm["always_templates"] + [tenant.macro_template]))
    template_ids = _template_ids(ctx, template_names)
    proxy_group = z.proxy_group_id(tenant.proxy_group_name)
    dtype = device.get("device_type") or {}
    params = {
        "host": zabbix_safe(f"{tenant.code}-{device['name']}"),
        "name": f"{device['name']} ({tenant.name})"[:128],
        "status": zstatus,
        "inventory_mode": 0,
        "inventory": {
            "serialno_a": device.get("serial") or "",
            "asset_tag": device.get("asset_tag") or "",
            "model": dtype.get("model", ""),
            "vendor": (dtype.get("manufacturer") or {}).get("name", ""),
            "location": (device.get("location") or {}).get("name", ""),
            "os": (device.get("platform") or {}).get("name", ""),
        },
    }
    if proxy_group:
        params.update({"monitored_by": 2, "proxy_groupid": proxy_group})
    else:
        params.update({"monitored_by": 0})
    desired_tags = _desired_tags(device, tenant, asset_id, itemtype)

    if host is None:
        params.update({
            "groups": [{"groupid": gid} for gid in group_ids],
            "templates": [{"templateid": tid} for tid in template_ids],
            "interfaces": [_interface(device, ip, tenant)],
            "tags": desired_tags,
        })
        if settings.dry_run:
            return "", "created"
        try:
            hostid = z.host_create(params)
        except APIError as exc:
            existing = z.host_by_name(params["host"]) if "already exists" in str(exc) else None
            if not existing:
                raise
            log.warning("Zabbix: host %s already exists without msp-glue tags; adopting it", params["host"])
            return _ensure_host(ctx, device, existing, ip, zstatus, asset_id, itemtype)
        log.info("Zabbix: created host %s", params["host"])
        return hostid, "created"

    hostid = host["hostid"]
    root = zm["customer_group_root"] + "/"
    keep_groups = [g["groupid"] for g in host.get("hostgroups", []) if not g["name"].startswith(root)]
    current_templates = [t["templateid"] for t in host.get("parentTemplates", [])]
    keep_tags = [t for t in host.get("tags", []) if t["tag"] not in MANAGED_TAGS]
    params.update({
        "hostid": hostid,
        "groups": [{"groupid": gid} for gid in dict.fromkeys(group_ids + keep_groups)],
        "templates": [{"templateid": tid} for tid in dict.fromkeys(current_templates + template_ids)],
        "tags": desired_tags + keep_tags,
    })
    if not settings.dry_run:
        z.host_update(params)
        _ensure_main_interface(z, host, _interface(device, ip, tenant))
    return hostid, "updated"


def _ensure_main_interface(z: Zabbix, host: dict, wanted: dict) -> None:
    for iface in host.get("interfaces", []):
        if str(iface["type"]) == str(wanted["type"]) and str(iface["main"]) == "1":
            if iface["ip"] != wanted["ip"] or iface["port"] != wanted["port"]:
                z.interface_update({"interfaceid": iface["interfaceid"], "ip": wanted["ip"], "port": wanted["port"]})
            return
    z.call("hostinterface.create", {"hostid": host["hostid"], **wanted})


def _retire_host(ctx: _Context, host: dict, reason: str = "removed from Nautobot") -> None:
    """Disable (never delete) so history and SLA reports are kept. Mark the GLPI asset."""
    log.info("Zabbix: disabling host %s (%s)", host["name"], reason)
    if settings.dry_run:
        return
    tags = [t for t in host.get("tags", []) if t["tag"] != "msp_retired"] + [{"tag": "msp_retired", "value": reason}]
    ctx.z.host_update({"hostid": host["hostid"], "status": 1, "tags": tags})
    asset_id, itemtype = _tag(host, "glpi_asset_id"), _tag(host, "glpi_itemtype")
    if ctx.g and asset_id and itemtype and reason == "removed from Nautobot":
        state = _dropdown(ctx, "State", settings.mappings["glpi"]["state_by_status"].get("Decommissioning", ""))
        if state:
            ctx.g.update(itemtype, int(asset_id), {"states_id": state})


def _push_inventory(tenant: Tenant, devices: list[dict]) -> None:
    if not tenant.custom_fields.get("msp_semaphore_project_id") or settings.dry_run:
        return
    from .inventory import render_inventory
    sem = clients.semaphore()
    project_id = int(tenant.custom_fields["msp_semaphore_project_id"])
    keys = [k for k in (sem.request("GET", f"project/{project_id}/keys") or []) if k["name"] == "device-login"]
    if keys:
        sem.ensure_inventory(project_id, f"{tenant.code} devices", render_inventory(tenant, devices), keys[0]["id"])
