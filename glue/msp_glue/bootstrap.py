"""One-time platform set-up. Every step is idempotent: running it again changes nothing
that is already in place, so it is safe to re-run after an upgrade or a partial failure.

Two stages:
  tokens    needs the admin passwords of each tool, creates the service accounts msp-glue
            uses and prints their API tokens for the .env file.
  platform  uses those tokens and creates the shared baseline objects in every tool.
"""

from __future__ import annotations

import base64
import logging
import secrets as pysecrets
from pathlib import Path

import yaml

from . import clients
from .clients.forgejo import Forgejo
from .clients.glpi import GLPI
from .clients.semaphore import Semaphore
from .clients.zabbix import PERM_READ, PERM_READ_WRITE, Zabbix
from .common import DEVICE_FIELDS, TENANT_FIELDS
from .http import APIError, make_session
from .settings import settings

log = logging.getLogger("msp_glue.bootstrap")

SEED_DIR = Path("/app/seed")


# =============================================================================
# Stage 1: service accounts and tokens
# =============================================================================
def create_tokens(zabbix_admin: tuple[str, str], glpi_admin: tuple[str, str], semaphore_admin: tuple[str, str],
                  forgejo_admin: tuple[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}

    # --- Zabbix: a Super admin service account for msp-glue, with an API token
    z = Zabbix.login(settings.zabbix_url, *zabbix_admin)
    role = z.call("role.get", {"filter": {"name": ["Super admin role"]}, "output": ["roleid"]})[0]["roleid"]
    admin_group = z.call("usergroup.get", {"filter": {"name": ["Zabbix administrators"]}, "output": ["usrgrpid"]})[0]["usrgrpid"]
    user = z.call("user.get", {"filter": {"username": ["svc-msp-glue"]}, "output": ["userid"]})
    if user:
        userid = user[0]["userid"]
    else:
        userid = z.call("user.create", {
            "username": "svc-msp-glue", "name": "msp-glue", "surname": "service account",
            "passwd": pysecrets.token_urlsafe(24) + "aA1!", "roleid": role,
            "usrgrps": [{"usrgrpid": admin_group}],
        })["userids"][0]
    old = z.call("token.get", {"userids": [userid], "filter": {"name": "msp-glue"}, "output": ["tokenid"]})
    if old:
        z.call("token.delete", [t["tokenid"] for t in old])
    tokenid = z.call("token.create", {"name": "msp-glue", "userid": userid})["tokenids"][0]
    out["ZABBIX_API_TOKEN"] = z.call("token.generate", [tokenid])[0]["token"]

    # --- GLPI: a Super-Admin service account with a personal API token
    g = GLPI(settings.glpi_url, user_token="", app_token=settings.glpi_app_token)
    session = make_session()
    headers = {"Content-Type": "application/json"}
    if settings.glpi_app_token:
        headers["App-Token"] = settings.glpi_app_token
    auth = base64.b64encode(f"{glpi_admin[0]}:{glpi_admin[1]}".encode()).decode()
    response = session.get(f"{g.base}/initSession", headers={**headers, "Authorization": f"Basic {auth}"}, timeout=30)
    if response.status_code >= 400:
        raise APIError(f"GLPI login failed ({response.status_code}): {response.text[:300]}. "
                       "Enable 'Login with credentials' in Setup > General > API for the bootstrap.")
    g.session_token = response.json()["session_token"]
    g.request("POST", "changeActiveEntities", {"entities_id": 0, "is_recursive": True})
    super_admin = g.find("Profile", name="Super-Admin")[0]["id"]
    out["GLPI_USER_TOKEN"] = _glpi_service_user(g, "svc-msp-glue", "msp-glue", super_admin)

    # A second, least-privilege GLPI account for the Zabbix ticketing webhook.
    out.update(_glpi_webhook_account(g))
    g.close()

    # --- Semaphore: API token for the admin account
    sem = Semaphore.login(settings.semaphore_url, *semaphore_admin)
    out["SEMAPHORE_API_TOKEN"] = sem.session.headers["Authorization"].split(" ", 1)[1]

    # --- Forgejo: API token for the admin account (repository and organisation scopes)
    fsession = make_session()
    name = f"msp-glue-{pysecrets.token_hex(3)}"
    response = fsession.post(
        f"{settings.forgejo_url.rstrip('/')}/api/v1/users/{forgejo_admin[0]}/tokens",
        auth=forgejo_admin, json={"name": name, "scopes": ["write:repository", "write:organization", "read:user"]},
        timeout=30,
    )
    if response.status_code >= 400:
        raise APIError(f"Forgejo token creation failed: {response.status_code} {response.text[:300]}")
    out["FORGEJO_API_TOKEN"] = response.json()["sha1"]
    return out


def _glpi_webhook_account(g: GLPI) -> dict[str, str]:
    """GLPI account used by the Zabbix webhook: may create and update tickets, nothing else."""
    profile_id = g.ensure("Profile", {"name": "MSP monitoring integration"}, {"interface": "central"})
    # Rights are bit masks (see GLPI source: CommonDBTM, CommonITILObject, ITILSubItemRights).
    rights = {
        "ticket": 1 | 2 | 4 | 1024,            # read own, update, create, see all tickets
        "followup": 1 | 1024 | 4096 | 8192,    # see public, update all, add to all tickets, see private
        "networking": 1,                       # read network equipment (to link the asset)
        "computer": 1,                         # read computers (to link the asset)
    }
    for rights_name, value in rights.items():
        found = g.find("ProfileRight", profiles_id=profile_id, name=rights_name)
        if found:
            g.update("ProfileRight", int(found[0]["id"]), {"rights": value})
    token = _glpi_service_user(g, "svc-zabbix-webhook", "Zabbix webhook", profile_id)
    return {"GLPI_ZABBIX_WEBHOOK_TOKEN": token}


def _glpi_service_user(g: GLPI, login: str, label: str, profile_id: int) -> str:
    """Create (or re-key) a GLPI service account and return a working API token.

    GLPI never returns tokens through the API, so msp-glue generates the token itself and
    stores it on the account (GLPI encrypts it), then proves it works with a test login.
    """
    token = pysecrets.token_hex(20)
    users = g.find("User", name=login)
    trashed = [] if users else g.find("User", _deleted=True, name=login)
    if trashed:
        g.update("User", int(trashed[0]["id"]), {"is_deleted": 0})
        users = trashed
    if users:
        uid = int(users[0]["id"])
        g.update("User", uid, {"api_token": token})
    else:
        password = pysecrets.token_urlsafe(24) + "aA1!"   # never used: the account logs in with its token
        uid = g.add("User", {"name": login, "realname": label, "firstname": "service account", "is_active": 1,
                             "password": password, "password2": password, "api_token": token})
    if not g.find("Profile_User", users_id=uid, profiles_id=profile_id, entities_id=0):
        g.add("Profile_User", {"users_id": uid, "profiles_id": profile_id, "entities_id": 0, "is_recursive": 1})
    g.update("User", uid, {"profiles_id": profile_id, "entities_id": 0})
    probe = GLPI(settings.glpi_url, token, settings.glpi_app_token)
    try:
        probe.open()
    except APIError as exc:
        raise APIError(f"GLPI refused the new token for {login}. If the account already existed with "
                       f"Super-Admin rights, reset its API token in Administration > Users and paste it "
                       f"into .env by hand. Detail: {exc}") from exc
    finally:
        probe.close()
    return token


# =============================================================================
# Stage 2: shared baseline objects
# =============================================================================
def platform(webhook_token: str = "") -> None:
    nautobot_baseline()
    forgejo_baseline()
    golden_config_baseline()
    glpi_ids = glpi_baseline()
    zabbix_baseline(webhook_token, glpi_ids)
    log.info("Bootstrap complete")


# =============================================================================
# Stage 3: replace the default administrator passwords
# =============================================================================
GLPI_DEMO_ACCOUNTS = ("tech", "normal", "post-only")


def harden(zabbix_admin_password: str, glpi_admin_password: str) -> dict[str, str]:
    """Give Zabbix "Admin" and GLPI "glpi" new passwords and switch off GLPI's demo accounts.

    Runs with msp-glue's own service tokens, so it works whatever the current passwords are.
    Safe to run again: it sets the same passwords and leaves disabled accounts disabled.
    """
    result: dict[str, str] = {}
    if zabbix_admin_password:
        z = clients.zabbix()
        users = z.call("user.get", {"filter": {"username": ["Admin"]}, "output": ["userid"]})
        if not users:
            raise APIError("Zabbix user 'Admin' not found")
        z.call("user.update", {"userid": users[0]["userid"], "passwd": zabbix_admin_password})
        Zabbix.login(settings.zabbix_url, "Admin", zabbix_admin_password)  # raises if the new password fails
        result["zabbix Admin"] = "password replaced and tested"
    with clients.glpi() as g:
        if glpi_admin_password:
            found = g.find("User", name="glpi")
            if not found:
                raise APIError("GLPI user 'glpi' not found")
            g.update("User", int(found[0]["id"]), {"password": glpi_admin_password, "password2": glpi_admin_password})
            result["glpi glpi"] = "password replaced"
        for login in GLPI_DEMO_ACCOUNTS:
            for user in g.find("User", name=login):
                if str(user.get("is_active")) != "0":
                    g.update("User", int(user["id"]), {"is_active": 0})
                result[f"glpi {login}"] = "disabled"
    for key, value in result.items():
        log.info("%s: %s", key, value)
    return result


# --------------------------------------------------------------------- Nautobot
PLATFORMS = [
    ("Cisco", "Cisco IOS", "cisco_ios"), ("Cisco", "Cisco IOS-XE", "cisco_xe"), ("Cisco", "Cisco NX-OS", "cisco_nxos"),
    ("Cisco", "Cisco ASA", "cisco_asa"), ("Fortinet", "FortiOS", "fortinet"), ("Juniper", "Junos", "juniper_junos"),
    ("Arista", "Arista EOS", "arista_eos"), ("MikroTik", "RouterOS", "mikrotik_routeros"),
    ("Huawei", "Huawei VRP", "huawei_vrp"), ("HPE Aruba", "AOS-CX", "aruba_aoscx"),
    ("HPE Aruba", "Comware", "hp_comware"), ("TP-Link", "TP-Link JetStream", "tplink_jetstream"),
    ("Generic", "Linux", "linux"), ("Generic", "Windows Server", "windows"),
]
ROLES = [("router", "0d6efd"), ("switch", "198754"), ("firewall", "dc3545"), ("access-point", "6f42c1"),
         ("server", "fd7e14"), ("hypervisor", "20c997"), ("sd-wan-edge", "0dcaf0")]


def nautobot_baseline() -> None:
    nb = clients.nautobot()
    tenant_group = settings.mappings["tenancy"]["tenant_group"]
    nb.ensure("tenancy/tenant-groups/", {"name": tenant_group}, {"name": tenant_group,
              "description": "Customers managed by IOH. msp-glue provisions every tenant in this group."})
    _ensure_custom_fields(nb, "tenancy.tenant", TENANT_FIELDS)
    _ensure_custom_fields(nb, "dcim.device", DEVICE_FIELDS)

    manufacturers = {}
    for maker, name, driver in PLATFORMS:
        if maker not in manufacturers:
            manufacturers[maker] = nb.ensure("dcim/manufacturers/", {"name": maker}, {"name": maker})["id"]
        nb.ensure("dcim/platforms/", {"name": name},
                  {"name": name, "network_driver": driver, "manufacturer": manufacturers[maker]})
    for role, color in ROLES:
        nb.ensure("extras/roles/", {"name": role}, {"name": role, "color": color, "content_types": ["dcim.device"]})
    region = nb.ensure("dcim/location-types/", {"name": "Region"},
                       {"name": "Region", "nestable": True, "content_types": []})
    nb.ensure("dcim/location-types/", {"name": "Site"},
              {"name": "Site", "parent": region["id"],
               "content_types": ["dcim.device", "ipam.prefix", "ipam.vlan", "circuits.circuittermination"]})

    # Webhook so tenant and device changes reach msp-glue within seconds.
    if settings.nautobot_webhook_secret:
        hook = nb.first("extras/webhooks/", name="msp-glue")
        payload = {
            "name": "msp-glue", "content_types": ["tenancy.tenant", "dcim.device"],
            "type_create": True, "type_update": True, "type_delete": True, "enabled": True,
            "payload_url": f"{settings.hook_base}/hooks/nautobot", "http_method": "POST",
            "http_content_type": "application/json", "secret": settings.nautobot_webhook_secret,
            "ssl_verification": False,
        }
        if hook:
            nb.patch("extras/webhooks/", hook["id"], payload)
        else:
            nb.create("extras/webhooks/", payload)


def _ensure_custom_fields(nb, content_type: str, definitions: dict) -> None:
    # The custom-fields endpoint cannot be filtered by key, so read them all once.
    by_key = {cf["key"]: cf for cf in nb.list("extras/custom-fields/")}
    for key, spec in definitions.items():
        existing = by_key.get(key)
        if existing:
            if content_type not in existing.get("content_types", []):
                nb.patch("extras/custom-fields/", existing["id"],
                         {"content_types": sorted(set(existing["content_types"]) | {content_type})})
            continue
        payload = {"key": key, "label": spec["label"], "type": spec["type"], "content_types": [content_type],
                   "description": spec.get("description", "Managed by msp-glue." if spec.get("managed") else ""),
                   "grouping": "Managed services"}
        if "default" in spec and not spec.get("choices"):
            payload["default"] = spec["default"]
        created = nb.create("extras/custom-fields/", payload)
        for weight, choice in enumerate(spec.get("choices", []), start=1):
            nb.create("extras/custom-field-choices/", {"custom_field": created["id"], "value": choice,
                                                       "weight": weight * 100})
        if spec.get("choices") and "default" in spec:
            # A select field's default must be one of its choices, so set it after the choices exist.
            nb.patch("extras/custom-fields/", created["id"], {"default": spec["default"]})


# ---------------------------------------------------------------------- Forgejo
def forgejo_baseline() -> None:
    fj = clients.forgejo()
    org = settings.forgejo_org
    fj.ensure_org(org)
    fj.ensure_repo(org, "playbooks", "Ansible playbooks run by Semaphore for every customer")
    fj.ensure_repo(org, "golden-config", "Golden Config Jinja templates and compliance rules")
    fj.ensure_repo(org, "intended-configs", "Intended configurations rendered by Golden Config")
    _seed_repo(fj, org, "playbooks", SEED_DIR / "playbooks")
    _seed_repo(fj, org, "golden-config", SEED_DIR / "golden-config")


def _seed_repo(fj: Forgejo, org: str, repo: str, folder: Path) -> None:
    """Upload the starter files into a new repository, once.

    A marker file (.msp-seeded) records that seeding happened, so files you later change or
    delete in Git are never put back by a re-run of the bootstrap.
    """
    if not folder.exists():
        log.warning("Seed folder %s not found, skipping %s", folder, repo)
        return
    if fj.request("GET", f"repos/{org}/{repo}/contents/.msp-seeded", ok404=True) is not None:
        return
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if fj.request("GET", f"repos/{org}/{repo}/contents/{rel}", ok404=True) is not None:
            continue
        fj.request("POST", f"repos/{org}/{repo}/contents/{rel}", {
            "content": base64.b64encode(path.read_bytes()).decode(), "message": f"Seed {rel}", "branch": "main",
        })
        log.info("Forgejo: seeded %s/%s:%s", org, repo, rel)
    fj.request("POST", f"repos/{org}/{repo}/contents/.msp-seeded", {
        "content": base64.b64encode(b"Starter content uploaded by mspctl bootstrap.\n").decode(),
        "message": "Mark repository as seeded", "branch": "main",
    })


# ---------------------------------------------------------------- Golden Config
def golden_config_baseline() -> None:
    nb = clients.nautobot()
    gc = settings.mappings["golden_config"]
    git_sg = ensure_git_secrets_group(nb)
    org = settings.forgejo_org
    templates_repo = nb.ensure("extras/git-repositories/", {"name": "golden-config"}, {
        "name": "golden-config", "slug": "golden_config", "branch": "main", "secrets_group": git_sg,
        "remote_url": f"{settings.forgejo_url.rstrip('/')}/{org}/golden-config.git",
        "provided_contents": ["nautobot_golden_config.jinjatemplate", "nautobot_golden_config.pluginproperties"],
    })
    intended_repo = nb.ensure("extras/git-repositories/", {"name": "intended-configs"}, {
        "name": "intended-configs", "slug": "intended_configs", "branch": "main", "secrets_group": git_sg,
        "remote_url": f"{settings.forgejo_url.rstrip('/')}/{org}/intended-configs.git",
        "provided_contents": ["nautobot_golden_config.intendedconfigs"],
    })
    # Golden Config installs a catch-all "Default Settings" (scope: every device, no paths). While it
    # exists, a job run for one customer also picks up that customer's servers and planned devices and
    # fails on them with E3018. Customer settings replace it, so remove it once, if still untouched.
    for setting in nb.list("plugins/golden-config/golden-config-settings/", depth=1):
        group = setting.get("dynamic_group") or {}
        if setting.get("name") == "Default Settings" and group.get("filter") in ({}, None) \
                and not setting.get("backup_path_template"):
            log.info("Golden Config: removing the catch-all Default Settings")
            nb.request("DELETE", f"plugins/golden-config/golden-config-settings/{setting['id']}/")
            if group.get("id"):
                nb.request("DELETE", f"extras/dynamic-groups/{group['id']}/")
    # Nautobot installs app jobs disabled; switch the Golden Config jobs on.
    for job in nb.list("extras/jobs/", module_name="nautobot_golden_config.jobs"):
        if not job.get("enabled"):
            nb.patch("extras/jobs/", job["id"], {"enabled": True})
    # Pull both repositories so templates and compliance rules are loaded now.
    nb.sync_git_repository(templates_repo)
    nb.sync_git_repository(intended_repo)
    query_file = SEED_DIR / "golden-config" / "graphql" / "sotagg.graphql"
    query = query_file.read_text() if query_file.exists() else DEFAULT_SOTAGG_QUERY
    saved = nb.ensure("extras/graphql-queries/", {"name": gc["sotagg_query_name"]},
                      {"name": gc["sotagg_query_name"], "query": query})
    if saved.get("query", "").strip() != query.strip():
        nb.patch("extras/graphql-queries/", saved["id"], {"query": query})


def ensure_git_secrets_group(nb) -> str:
    """Secrets Group holding the Forgejo user name and token, read from files by Nautobot."""
    group = nb.ensure("extras/secrets-groups/", {"name": "forgejo-git"},
                      {"name": "forgejo-git", "description": "HTTP(S) credentials for the platform Git server"})
    for secret_type, filename in (("username", "forgejo_username"), ("token", "forgejo_token")):
        secret = nb.ensure("extras/secrets/", {"name": f"forgejo-{secret_type}"}, {
            "name": f"forgejo-{secret_type}", "provider": "text-file",
            "parameters": {"path": f"/run/msp-secrets/platform/{filename}"},
        })
        if not nb.first("extras/secrets-groups-associations/", secrets_group=group["id"], secret=secret["id"]):
            nb.create("extras/secrets-groups-associations/", {"secrets_group": group["id"], "secret": secret["id"],
                                                              "access_type": "HTTP(S)", "secret_type": secret_type})
    return group["id"]


DEFAULT_SOTAGG_QUERY = """query ($device_id: ID!) {
  device(id: $device_id) {
    hostname: name
    config_context
    tenant { name _custom_field_data }
    location { name parent { name } }
    role { name }
    platform { name network_driver }
    interfaces { name description enabled ip_addresses { address } }
  }
}
"""


# ------------------------------------------------------------------------- GLPI
def glpi_baseline() -> dict:
    m = settings.mappings["glpi"]
    with clients.glpi() as g:
        msp_entity = g.ensure("Entity", {"name": m["msp_entity"], "entities_id": 0},
                              {"comment": "Parent of every managed customer. Created by msp-glue."})
        g.ensure("RequestType", {"name": m["monitoring_request_type"]}, {"is_active": 1, "is_ticketheader": 1})
        noc = g.ensure("Group", {"name": m["noc_group"], "entities_id": 0},
                       {"is_recursive": 1, "is_assign": 1, "is_requester": 0, "is_notify": 1})
        cats = _ensure_categories(g, list(m["categories"].values()) +
                                  list(settings.mappings["automation"]["change_templates"].keys()))
        for state in m["state_by_status"].values():
            g.ensure("State", {"name": state, "entities_id": 0}, {"is_recursive": 1})
        cal_ids = {name: _ensure_calendar(g, name, spec) for name, spec in m["calendars"].items()}
        for tier, spec in m["service_tiers"].items():
            _ensure_tier_slas(g, tier, spec, cal_ids[spec["calendar"]])
        _ensure_monitoring_rule(g, noc, cats[m["categories"]["monitoring"]])
        _ensure_glpi_webhook(g)
        request_type = g.find("RequestType", name=m["monitoring_request_type"])[0]["id"]
        log.info("GLPI baseline ready (managed services entity id %s)", msp_entity)
        return {"msp_entity": msp_entity, "request_type": request_type}


def _ensure_categories(g: GLPI, full_names: list[str]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for full in full_names:
        parent = 0
        path = []
        for part in [p.strip() for p in full.split(">")]:
            path.append(part)
            key = " > ".join(path)
            if key not in ids:
                ids[key] = g.ensure("ITILCategory", {"name": part, "itilcategories_id": parent},
                                    {"entities_id": 0, "is_recursive": 1, "is_helpdeskvisible": 1,
                                     "is_incident": 1, "is_request": 1, "is_problem": 1, "is_change": 1})
            parent = ids[key]
    return ids


def _ensure_calendar(g: GLPI, name: str, spec: dict) -> int:
    cal = g.ensure("Calendar", {"name": name, "entities_id": 0}, {"is_recursive": 1})
    existing = g.request("GET", f"Calendar/{cal}/CalendarSegment") or []
    if not existing:
        for day in spec["days"]:
            g.add("CalendarSegment", {"calendars_id": cal, "entities_id": 0, "is_recursive": 1, "day": day,
                                      "begin": spec["begin"], "end": spec["end"]})
    return cal


def _ensure_tier_slas(g: GLPI, tier: str, spec: dict, calendar_id: int) -> None:
    slm = g.ensure("SLM", {"name": f"Managed services {tier}", "entities_id": 0},
                   {"is_recursive": 1, "calendars_id": calendar_id, "use_ticket_calendar": 0})
    # type 1 = time to own, type 0 = time to resolve
    g.ensure("SLA", {"name": f"{tier.title()} time to own", "slms_id": slm},
             {"entities_id": 0, "is_recursive": 1, "type": 1, "number_time": spec["own_minutes"],
              "definition_time": "minute", "calendars_id": calendar_id})
    g.ensure("SLA", {"name": f"{tier.title()} time to resolve", "slms_id": slm},
             {"entities_id": 0, "is_recursive": 1, "type": 0, "number_time": spec["resolve_hours"],
              "definition_time": "hour", "calendars_id": calendar_id})


def ensure_ticket_rule(g: GLPI, name: str, criteria: list[tuple[str, int, object]],
                       actions: list[tuple[str, str, object]], ranking: int = 10) -> int:
    """Create (or reset) a ticket business rule at the root entity, applied to all sub-entities.
    criteria: (field, condition, pattern) where condition 0 = is; actions: (action_type, field, value)."""
    found = g.find("RuleTicket", name=name)
    if found:
        rule_id = int(found[0]["id"])
        for crit in g.request("GET", f"RuleTicket/{rule_id}/RuleCriteria") or []:
            g.request("DELETE", f"RuleCriteria/{crit['id']}", {"input": {"id": crit["id"]}, "force_purge": True})
        for act in g.request("GET", f"RuleTicket/{rule_id}/RuleAction") or []:
            g.request("DELETE", f"RuleAction/{act['id']}", {"input": {"id": act["id"]}, "force_purge": True})
    else:
        rule_id = g.add("RuleTicket", {"name": name, "entities_id": 0, "is_recursive": 1, "sub_type": "RuleTicket",
                                       "match": "AND", "is_active": 1, "condition": 1, "ranking": ranking,
                                       "description": "Managed by msp-glue"})
    for field, condition, pattern in criteria:
        g.add("RuleCriteria", {"rules_id": rule_id, "criteria": field, "condition": condition, "pattern": pattern})
    for action_type, field, value in actions:
        g.add("RuleAction", {"rules_id": rule_id, "action_type": action_type, "field": field, "value": value})
    return rule_id


def _ensure_monitoring_rule(g: GLPI, noc_group: int, category: int) -> None:
    req_type = g.find("RequestType", name=settings.mappings["glpi"]["monitoring_request_type"])[0]["id"]
    ensure_ticket_rule(
        g, "MSP: monitoring alerts go to the NOC",
        criteria=[("requesttypes_id", 0, req_type)],
        actions=[("assign", "_groups_id_assign", noc_group), ("assign", "itilcategories_id", category)],
        ranking=1,
    )


def _ensure_glpi_webhook(g: GLPI) -> None:
    if not settings.glpi_webhook_secret:
        return
    fields = {"is_active": 1, "itemtype": "Change", "event": "update", "url": f"{settings.hook_base}/hooks/glpi",
              "http_method": "post", "secret": settings.glpi_webhook_secret, "entities_id": 0, "is_recursive": 1,
              "use_default_payload": 1, "sent_try": 3}
    g.ensure("Webhook", {"name": "msp-glue: approved changes"}, fields)


# ----------------------------------------------------------------------- Zabbix
def zabbix_baseline(webhook_token: str = "", glpi_ids: dict | None = None) -> None:
    zm = settings.mappings["zabbix"]
    z = clients.zabbix()
    z.ensure_templategroup(zm["macro_template_group"])
    z.ensure_hostgroup(zm["customer_group_root"])
    root = z.hostgroups_with_prefix(zm["customer_group_root"])
    z.ensure_usergroup(zm["noc_usergroup"], [(g["groupid"], PERM_READ_WRITE) for g in root])
    z.ensure_usergroup(zm["service_usergroup"], [(g["groupid"], PERM_READ) for g in root], gui_access=3)
    pg = z.ensure_proxy_group(zm["default_proxy_group"])
    z.ensure_active_proxy("proxy-central-01", proxy_groupid=pg, local_address="zabbix-proxy-central")
    z.ensure_global_macro("{$ZABBIX.URL}", settings.zabbix_public_url or settings.zabbix_url)
    media_file = SEED_DIR / "zabbix" / "media_glpi_msp.yaml"
    if media_file.exists():
        # The import resets the webhook's settings to the placeholders in the file and disables it.
        # Keep what an earlier run set (GLPI address, tokens, entity, enabled) unless a new token is given.
        before = z.call("mediatype.get", {"filter": {"name": ["GLPI MSP"]}, "output": ["status", "parameters"]})
        z.call("configuration.import", {
            "format": "yaml", "source": media_file.read_text(),
            "rules": {"mediaTypes": {"createMissing": True, "updateExisting": True}},
        })
        if before and not webhook_token:
            _restore_media_settings(z, before[0])
    _ensure_ticket_action(z, webhook_token, glpi_ids or {})


MEDIA_SETTINGS = ("glpi_url", "glpi_public_url", "glpi_user_token", "glpi_app_token",
                  "glpi_default_entity_id", "glpi_requesttype_id")


def _restore_media_settings(z: Zabbix, before: dict) -> None:
    old = {p["name"]: p["value"] for p in before.get("parameters", [])}
    current = z.call("mediatype.get", {"filter": {"name": ["GLPI MSP"]}, "output": ["mediatypeid", "parameters"]})[0]
    params = current["parameters"]
    for p in params:
        if p["name"] in MEDIA_SETTINGS and p["name"] in old:
            p["value"] = old[p["name"]]
    z.call("mediatype.update", {"mediatypeid": current["mediatypeid"], "status": before["status"], "parameters": params})
    log.info("Zabbix: kept the existing settings of media type GLPI MSP (no --webhook-token given)")


def ensure_glue_user_in_noc(z: Zabbix, noc_group: str | None = None) -> None:
    """The MSP Console acknowledges problems as svc-msp-glue. As a member of the NOC user group too, its
    name shows in the GLPI follow-up and the chat message instead of "Inaccessible user"."""
    if noc_group is None:
        found = z.call("usergroup.get", {"filter": {"name": [settings.mappings["zabbix"]["noc_usergroup"]]},
                                         "output": ["usrgrpid"]})
        if not found:
            return
        noc_group = found[0]["usrgrpid"]
    glue_user = z.call("user.get", {"filter": {"username": ["svc-msp-glue"]}, "output": ["userid"],
                                    "selectUsrgrps": ["usrgrpid"]})
    if glue_user:
        current = {g["usrgrpid"] for g in glue_user[0].get("usrgrps", [])}
        if noc_group not in current:
            z.call("user.update", {"userid": glue_user[0]["userid"],
                                   "usrgrps": [{"usrgrpid": g} for g in sorted(current | {noc_group})]})
            log.info("Zabbix: svc-msp-glue added to the NOC user group")


def _ensure_ticket_action(z: Zabbix, webhook_token: str, glpi_ids: dict) -> None:
    """Service user + media + action: problems on managed hosts open GLPI incidents."""
    zm = settings.mappings["zabbix"]
    media = z.call("mediatype.get", {"filter": {"name": ["GLPI MSP"]}, "output": ["mediatypeid"]})
    if not media:
        log.warning("Zabbix media type 'GLPI MSP' not imported; skipping the ticket action")
        return
    mediatypeid = media[0]["mediatypeid"]
    if webhook_token:
        params = z.call("mediatype.get", {"mediatypeids": [mediatypeid], "output": ["parameters"]})[0]["parameters"]
        wanted = {"glpi_url": settings.glpi_url, "glpi_public_url": settings.glpi_public_url or settings.glpi_url,
                  "glpi_user_token": webhook_token, "glpi_app_token": settings.glpi_app_token,
                  "glpi_default_entity_id": str(glpi_ids.get("msp_entity", 0)),
                  "glpi_requesttype_id": str(glpi_ids.get("request_type", ""))}
        for p in params:
            if p["name"] in wanted:
                p["value"] = wanted[p["name"]]
        z.call("mediatype.update", {"mediatypeid": mediatypeid, "status": 0, "parameters": params})
    service_group = z.call("usergroup.get", {"filter": {"name": [zm["service_usergroup"]]}, "output": ["usrgrpid"]})[0]["usrgrpid"]
    # Also a member of the NOC group: Zabbix only shows an engineer's name ({USER.FULLNAME}) to
    # notification recipients who share a user group with that engineer. Frontend access stays
    # disabled because the service group disables it and the most restrictive setting wins.
    noc_group = z.call("usergroup.get", {"filter": {"name": [zm["noc_usergroup"]]}, "output": ["usrgrpid"]})[0]["usrgrpid"]
    groups = [{"usrgrpid": service_group}, {"usrgrpid": noc_group}]
    ensure_glue_user_in_noc(z, noc_group)
    user_role = z.call("role.get", {"filter": {"name": ["User role"]}, "output": ["roleid"]})[0]["roleid"]
    user = z.call("user.get", {"filter": {"username": ["svc-glpi-webhook"]}, "output": ["userid"]})
    medias = [{"mediatypeid": mediatypeid, "sendto": "glpi", "active": 0, "severity": 63, "period": "1-7,00:00-24:00"}]
    if user:
        userid = user[0]["userid"]
        z.call("user.update", {"userid": userid, "medias": medias, "usrgrps": groups})
    else:
        userid = z.call("user.create", {"username": "svc-glpi-webhook", "name": "GLPI", "surname": "webhook",
                                        "passwd": pysecrets.token_urlsafe(24) + "aA1!", "roleid": user_role,
                                        "usrgrps": groups, "medias": medias})["userids"][0]
    action = {
        "name": "MSP: open GLPI incident",
        "eventsource": 0, "status": 0, "esc_period": "1h",
        "filter": {"evaltype": 1, "conditions": [
            # 26 = event tag value: tag "managed_by" equals "msp-glue" (set on every host msp-glue creates)
            {"conditiontype": 26, "operator": 0, "value": "msp-glue", "value2": "managed_by"},
            # 4 = trigger severity, operator 5 = "is greater than or equals", 3 = Average
            {"conditiontype": 4, "operator": 5, "value": "3"},
        ]},
        "operations": [{"operationtype": 0, "esc_step_from": 1, "esc_step_to": 1,
                        "opmessage": {"default_msg": 1, "mediatypeid": mediatypeid},
                        "opmessage_usr": [{"userid": userid}]}],
        # 11 / 12 = notify all involved users when the problem recovers or is updated
        "recovery_operations": [{"operationtype": 11, "opmessage": {"default_msg": 1}}],
        "update_operations": [{"operationtype": 12, "opmessage": {"default_msg": 1}}],
    }
    existing = z.call("action.get", {"filter": {"name": [action["name"]]}, "output": ["actionid"]})
    if existing:
        action["actionid"] = existing[0]["actionid"]
        action.pop("eventsource")
        z.call("action.update", action)
    else:
        z.call("action.create", action)
