"""Customer (tenant) onboarding: one Nautobot tenant becomes a customer in every tool.

Every step is idempotent. Running it again repairs anything missing or changed and
never duplicates objects, so the periodic reconciliation simply calls ensure_tenant().
"""

from __future__ import annotations

import logging
import os
import zlib
from datetime import datetime, timedelta, timezone

from . import clients, dashboards
from .bootstrap import ensure_git_secrets_group, ensure_ticket_rule
from .clients.zabbix import PERM_READ, PERM_READ_WRITE, Zabbix
from .common import Tenant
from .http import APIError
from .settings import settings

log = logging.getLogger("msp_glue.tenants")


def managed_tenants(nb=None) -> list[Tenant]:
    nb = nb or clients.nautobot()
    group = settings.mappings["tenancy"]["tenant_group"]
    raw = nb.list("tenancy/tenants/", tenant_group=group)
    return [Tenant.from_nautobot(t) for t in raw]


def get_tenant(identifier: str, nb=None) -> Tenant | None:
    """Find a managed tenant by Nautobot id, MSP code or name."""
    for tenant in managed_tenants(nb):
        if identifier in (tenant.id, tenant.code, tenant.name):
            return tenant
    return None


def ensure_tenant(tenant: Tenant) -> dict:
    """Provision or repair one customer everywhere. Returns the ids written back to Nautobot."""
    nb = clients.nautobot()
    if not tenant.valid:
        message = (f"Tenant '{tenant.name}' has no valid MSP customer code (custom field msp_code: lowercase "
                   "letters, digits and dashes, 2 to 20 characters). Nothing provisioned.")
        _write_back(nb, tenant, {"msp_provisioning_status": "error", "msp_provisioning_message": message})
        log.warning(message)
        return {}

    log.info("Provisioning customer %s (%s)", tenant.name, tenant.code)
    ids: dict = {}
    try:
        ids["msp_glpi_entity_id"] = ensure_glpi(tenant)
        tenant.glpi_entity_id = ids["msp_glpi_entity_id"]
        ids["msp_zabbix_hostgroup_id"] = ensure_zabbix(tenant)
        ensure_golden_config(tenant, nb)
        ids["msp_semaphore_project_id"] = ensure_semaphore(tenant)
        ids["msp_provisioning_status"] = "provisioned"
        # Keep the message stable between runs: a changing value would trigger another webhook.
        ids["msp_provisioning_message"] = "OK"
        try:
            dashboards.ensure_customer_dashboards(tenant)
        except Exception as exc:  # noqa: BLE001 - dashboards are optional; never block onboarding
            log.warning("Customer dashboards for %s not ready: %s", tenant.code, exc)
            ids["msp_provisioning_message"] = f"OK, dashboards pending: {exc}"[:250]
    except Exception as exc:  # noqa: BLE001 - record any failure on the tenant for the engineer to see
        log.exception("Provisioning of %s failed", tenant.code)
        ids["msp_provisioning_status"] = "error"
        ids["msp_provisioning_message"] = (f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
                                           f"{type(exc).__name__}: {exc}")[:250]
    _write_back(nb, tenant, ids)
    return ids


def _write_back(nb, tenant: Tenant, values: dict) -> None:
    changed = {k: v for k, v in values.items() if tenant.custom_fields.get(k) != v}
    if changed and not settings.dry_run:
        nb.set_custom_fields("tenancy/tenants/", tenant.id, changed)
        tenant.custom_fields.update(changed)


# --------------------------------------------------------------------------- GLPI
def ensure_glpi(tenant: Tenant) -> int:
    m = settings.mappings["glpi"]
    with clients.glpi() as g:
        msp_entity = g.ensure("Entity", {"name": m["msp_entity"], "entities_id": 0})
        entity_fields = {
            # The inventory tag lets GLPI Agent inventories land in this customer's entity.
            "tag": tenant.code,
            "comment": f"Managed customer {tenant.code}. Nautobot tenant {tenant.id}. "
                       f"IOH account {tenant.customer_id or 'n/a'}. Service tier {tenant.tier}.",
        }
        if tenant.glpi_entity_id and _exists(g, "Entity", tenant.glpi_entity_id):
            entity_id = int(tenant.glpi_entity_id)
            g.update("Entity", entity_id, {"name": tenant.name, **entity_fields})
        else:
            entity_id = g.ensure("Entity", {"name": tenant.name, "entities_id": msp_entity}, entity_fields)
            g.refresh_entities()   # let this session write into the entity it may have just created

        # Customer users (portal requesters) go in this group; GLPI rules can add it as observer.
        g.ensure("Group", {"name": f"{tenant.name} requesters", "entities_id": entity_id},
                 {"is_requester": 1, "is_assign": 0, "is_notify": 1, "is_recursive": 1})

        # Service levels: one rule per customer applies the tier's time-to-own and time-to-resolve.
        tier = tenant.tier if tenant.tier in m["service_tiers"] else "silver"
        tto = g.find("SLA", name=f"{tier.title()} time to own")
        ttr = g.find("SLA", name=f"{tier.title()} time to resolve")
        if tto and ttr:
            ensure_ticket_rule(
                g, f"MSP: service level for {tenant.code}",
                criteria=[("entities_id", 0, entity_id)],
                actions=[("assign", "slas_id_tto", int(tto[0]["id"])), ("assign", "slas_id_ttr", int(ttr[0]["id"]))],
                ranking=20,
            )
        else:
            log.warning("GLPI service levels for tier %s missing; run 'mspctl bootstrap platform'", tier)
        return entity_id


def _exists(g, itemtype: str, item_id) -> bool:
    try:
        return bool(g.get(itemtype, int(item_id)))
    except APIError:
        return False


# ------------------------------------------------------------------------- Zabbix
def ensure_zabbix(tenant: Tenant) -> str:
    zm = settings.mappings["zabbix"]
    z = clients.zabbix()

    # Rename the customer's host groups if the tenant was renamed in Nautobot.
    if tenant.zabbix_hostgroup_id:
        found = z.call("hostgroup.get", {"groupids": [tenant.zabbix_hostgroup_id], "output": ["groupid", "name"]})
        if found and found[0]["name"] != tenant.zabbix_group:
            old = found[0]["name"]
            for group in z.hostgroups_with_prefix(old):
                z.call("hostgroup.update", {"groupid": group["groupid"],
                                            "name": tenant.zabbix_group + group["name"][len(old):]})
            log.info("Zabbix: renamed host groups %s -> %s", old, tenant.zabbix_group)

    group_id = z.ensure_hostgroup(tenant.zabbix_group)
    template_group = z.ensure_templategroup(zm["macro_template_group"])
    z.ensure_macro_template(tenant.macro_template, template_group, tenant_macros(tenant),
                            description=f"Settings for customer {tenant.name}. Managed by msp-glue.")
    if tenant.proxy_group:
        z.ensure_proxy_group(tenant.proxy_group)
    refresh_zabbix_permissions(z, tenant)
    ensure_customer_sla(z, tenant)
    return group_id


def ensure_customer_sla(z: Zabbix, tenant: Tenant) -> None:
    """One service tree and one monthly SLA per customer, for availability reports.

    Child service: every open problem tagged tenant=<code> and scope=availability.
    Parent service: stays OK unless a child problem reaches the configured severity (High by
    default), so a slow ping or a missing SNMP value is not counted as downtime.
    The SLA reports the parent against the service tier's target.
    """
    cfg = settings.mappings["zabbix"].get("sla") or {}
    if not cfg:
        return
    severity = int(cfg.get("severity", 4))
    slo = str((cfg.get("slo") or {}).get(tenant.tier, "99.5"))
    child_name, parent_name = f"Customer {tenant.code}: availability problems", f"Customer {tenant.code}"
    child = {"name": child_name, "algorithm": 2, "sortorder": 0,
             "problem_tags": [{"tag": "tenant", "operator": 0, "value": tenant.code},
                              {"tag": "scope", "operator": 0, "value": "availability"}],
             "description": f"Availability problems of {tenant.name}. Managed by msp-glue."}
    found = z.call("service.get", {"filter": {"name": [child_name]}, "output": ["serviceid"]})
    if found:
        child_id = found[0]["serviceid"]
        z.call("service.update", {"serviceid": child_id, **child})
    else:
        child_id = z.call("service.create", child)["serviceids"][0]
    parent = {"name": parent_name, "algorithm": 0, "sortorder": 0,
              # "If at least 1 child service has <severity> status or above, set status to <severity>"
              "status_rules": [{"type": 0, "limit_value": 1, "limit_status": severity, "new_status": severity}],
              "tags": [{"tag": "msp_customer", "value": tenant.code}],
              "children": [{"serviceid": child_id}],
              "description": f"Service level of {tenant.name} ({tenant.tier}). Managed by msp-glue."}
    found = z.call("service.get", {"filter": {"name": [parent_name]}, "output": ["serviceid"]})
    if found:
        z.call("service.update", {"serviceid": found[0]["serviceid"], **parent})
    else:
        z.call("service.create", parent)
    sla_name = f"Customer {tenant.code} availability"
    first_of_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sla = {"name": sla_name, "period": 2, "slo": slo, "timezone": os.environ.get("TZ") or "Asia/Jakarta",
           "status": 1, "service_tags": [{"tag": "msp_customer", "operator": 0, "value": tenant.code}],
           "description": f"Monthly availability of {tenant.name}, service tier {tenant.tier}."}
    found = z.call("sla.get", {"filter": {"name": [sla_name]}, "output": ["slaid"]})
    if found:
        z.call("sla.update", {"slaid": found[0]["slaid"], **sla})
    else:
        z.call("sla.create", {**sla, "effective_date": int(first_of_month.timestamp())})
        log.info("Zabbix: created SLA %s (target %s%%)", sla_name, slo)


def tenant_macros(tenant: Tenant) -> list[dict]:
    """Macros stored on the customer's settings template (secret values are type 1, hidden in the UI)."""
    macros = [
        {"macro": "{$MSP.CUSTOMER}", "value": tenant.code, "description": tenant.name},
        {"macro": "{$MSP.TIER}", "value": tenant.tier},
    ]
    secret_files = {
        "{$SNMP_COMMUNITY}": "snmp_community",
        "{$SNMPV3_USER}": "snmpv3_user",
        "{$SNMPV3_AUTHPASS}": "snmpv3_authpass",
        "{$SNMPV3_PRIVPASS}": "snmpv3_privpass",
    }
    for macro, filename in secret_files.items():
        value = settings.tenant_secret(tenant.code, filename)
        if value:
            macros.append({"macro": macro, "value": value, "type": 1 if macro != "{$SNMPV3_USER}" else 0})
    return macros


def refresh_zabbix_permissions(z: Zabbix, tenant: Tenant | None = None) -> None:
    """Keep user group rights in line with the host groups that exist.

    Zabbix does not inherit rights from a parent group to its sub-groups through the API,
    so every sub-group (Customers/<name>/<role>) is listed explicitly.
    """
    zm = settings.mappings["zabbix"]
    every = z.hostgroups_with_prefix(zm["customer_group_root"])
    z.ensure_usergroup(zm["noc_usergroup"], [(g["groupid"], PERM_READ_WRITE) for g in every])
    z.ensure_usergroup(zm["service_usergroup"], [(g["groupid"], PERM_READ) for g in every], gui_access=3)
    tenants = [tenant] if tenant else managed_tenants()
    for t in tenants:
        if not t.valid:
            continue
        mine = z.hostgroups_with_prefix(t.zabbix_group)
        if mine:
            z.ensure_usergroup(f"Customer {t.code} {zm['customer_readonly_suffix']}",
                               [(g["groupid"], PERM_READ) for g in mine])


# ----------------------------------------------------------- Nautobot Golden Config
def ensure_golden_config(tenant: Tenant, nb) -> None:
    gc = settings.mappings["golden_config"]
    org = settings.forgejo_org
    repo_name = f"{gc['backup_repo_prefix']}{tenant.code}"

    fj = clients.forgejo()
    fj.ensure_org(org)
    fj.ensure_repo(org, repo_name, f"Configuration backups for {tenant.name}")

    git_sg = ensure_git_secrets_group(nb)
    backup_repo = nb.ensure("extras/git-repositories/", {"name": repo_name}, {
        "name": repo_name, "slug": repo_name.replace("-", "_"), "branch": "main", "secrets_group": git_sg,
        "remote_url": fj.clone_url(org, repo_name), "provided_contents": ["nautobot_golden_config.backupconfigs"],
    })

    nb.sync_git_repository(backup_repo, only_if_new=True)

    # Device logins for this customer, read by Nautobot from files under /run/msp-secrets/tenants/<code>/
    device_sg = ensure_device_secrets_group(nb, tenant)

    # Golden Config handles network devices that are in service and reachable: Active status, a primary
    # IP address and a platform listed in mappings.yaml. Servers are patched by Semaphore instead.
    scope = {"tenant": [tenant.name], "status": ["Active"], "has_primary_ip": True,
             "platform": list(gc.get("platforms", []))}
    group = nb.ensure("extras/dynamic-groups/", {"name": f"Customer {tenant.code} devices"}, {
        "name": f"Customer {tenant.code} devices", "content_type": "dcim.device", "group_type": "dynamic-filter",
        "filter": scope,
        "description": f"Network devices of {tenant.name} that Golden Config backs up and checks.",
    })
    if group.get("filter") != scope:
        nb.patch("extras/dynamic-groups/", group["id"], {"filter": scope})

    intended = nb.first("extras/git-repositories/", name="intended-configs")
    templates = nb.first("extras/git-repositories/", name="golden-config")
    query = nb.first("extras/graphql-queries/", name=gc["sotagg_query_name"])
    setting = {
        "name": f"Customer {tenant.code}", "slug": f"customer-{tenant.code}", "weight": 2000,
        "description": f"Golden Config scope for {tenant.name}",
        "dynamic_group": group["id"],
        "backup_repository": backup_repo["id"],
        "backup_path_template": gc["backup_path_template"],
        "backup_test_connectivity": True,
        "intended_repository": intended["id"] if intended else None,
        "intended_path_template": gc["intended_path_template"],
        "jinja_repository": templates["id"] if templates else None,
        "jinja_path_template": gc["jinja_path_template"],
        "sot_agg_query": query["id"] if query else None,
    }
    path = "plugins/golden-config/golden-config-settings/"
    existing = nb.first(path, slug=setting["slug"])
    if existing:
        nb.patch(path, existing["id"], setting)
    else:
        log.info("Nautobot: creating Golden Config settings for %s", tenant.code)
        nb.create(path, setting)
    ensure_gc_schedule(nb, tenant)
    return device_sg


def ensure_gc_schedule(nb, tenant: Tenant) -> None:
    """One nightly Golden Config run per customer (backup, intended, compliance).

    Separate schedules keep customers independent: a broken login or an unreachable site
    for one customer cannot stop the backups of the others. Start times are spread between
    01:00 and 03:59 so the runs do not all start together.
    """
    name = f"Golden Config nightly: {tenant.code}"
    if nb.first("extras/scheduled-jobs/", name=name):
        return
    group = nb.first("extras/dynamic-groups/", name=f"Customer {tenant.code} devices")
    members = nb.request("GET", f"extras/dynamic-groups/{group['id']}/members/", params={"limit": 1}) if group else None
    if not members or not members.get("count"):
        # A run with no network device in scope ends in error every night; wait for the first device.
        log.info("Golden Config schedule for %s waits until the customer has an active network device", tenant.code)
        return
    job = nb.first("extras/jobs/", module_name="nautobot_golden_config.jobs", job_class_name="AllDevicesGoldenConfig")
    if not job:
        log.warning("Golden Config job not found; nightly schedule for %s not created", tenant.code)
        return
    offset = zlib.crc32(tenant.code.encode()) % 180
    crontab = f"{offset % 60} {1 + offset // 60} * * *"
    nb.request("POST", f"extras/jobs/{job['id']}/run/", {
        "data": {"tenant": [tenant.id], "fail_job_on_task_failure": True,
                 "commit_message": f"Nightly backup for {tenant.code}"},
        "schedule": {"name": name, "interval": "custom", "crontab": crontab,
                     "start_time": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()},
    })
    log.info("Nautobot: scheduled %s at cron '%s' (platform time zone)", name, crontab)


def ensure_device_secrets_group(nb, tenant: Tenant) -> str:
    """Secrets Group with the customer's device login, read by Nautobot from text files.

    The enable secret is optional: it is only linked when the file device_enable exists,
    because Golden Config stops the whole job when a linked secret file is missing
    (Nornir uses the password as the enable secret when none is given).
    """
    name = f"{tenant.code}-device-login"
    group = nb.ensure("extras/secrets-groups/", {"name": name},
                      {"name": name, "description": f"Device logins for {tenant.name}"})
    for secret_type, filename, required in (("username", "device_username", True),
                                            ("password", "device_password", True),
                                            ("secret", "device_enable", False)):
        present = bool(settings.tenant_secret(tenant.code, filename))
        if required and not present:
            log.warning("Customer %s has no %s file in its secrets folder; Golden Config cannot log in",
                        tenant.code, filename)
        secret_name = f"{tenant.code}-device-{secret_type}"
        secret = nb.ensure("extras/secrets/", {"name": secret_name}, {
            "name": secret_name, "provider": "text-file",
            "parameters": {"path": f"/run/msp-secrets/tenants/{tenant.code}/{filename}"},
        })
        link = nb.first("extras/secrets-groups-associations/", secrets_group=group["id"], secret=secret["id"])
        if not required and not present:
            if link:
                nb.request("DELETE", f"extras/secrets-groups-associations/{link['id']}/")
            continue
        if not link:
            nb.create("extras/secrets-groups-associations/", {"secrets_group": group["id"], "secret": secret["id"],
                                                              "access_type": "Generic", "secret_type": secret_type})
    return group["id"]


# ---------------------------------------------------------------------- Semaphore
def ensure_semaphore(tenant: Tenant) -> int:
    sm = settings.mappings["semaphore"]
    sem = clients.semaphore()
    project_id = sem.ensure_project(tenant.code)

    git_user = _platform_secret("forgejo_username")
    git_token = _platform_secret("forgejo_token")
    git_key = sem.ensure_key(project_id, "forgejo-read", "login_password", login=git_user, secret=git_token)
    device_key = sem.ensure_key(
        project_id, "device-login", "login_password",
        login=settings.tenant_secret(tenant.code, "device_username"),
        secret=settings.tenant_secret(tenant.code, "device_password"),
    )
    org, repo = sm["playbook_repo"].split("/", 1)
    repository_id = sem.ensure_repository(project_id, "playbooks", f"{settings.forgejo_url.rstrip('/')}/{org}/{repo}.git",
                                          sm["playbook_branch"], git_key)
    from .inventory import render_inventory  # local import: inventory needs the device list
    inventory_id = sem.ensure_inventory(project_id, f"{tenant.code} devices", render_inventory(tenant), device_key)
    environment_id = sem.ensure_environment(project_id, "default", {"msp_customer": tenant.code,
                                                                    "msp_customer_name": tenant.name})
    for template in sm["templates"]:
        sem.ensure_template(project_id, template["name"], template["playbook"], inventory_id, repository_id,
                            environment_id, template.get("description", ""),
                            skip_galaxy_install=bool(sm.get("skip_galaxy_install", False)))
    return project_id


def _platform_secret(name: str) -> str:
    path = settings.secrets_dir.parent / "platform" / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
