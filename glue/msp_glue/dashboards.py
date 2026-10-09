"""Customer dashboards (phase 3): one Grafana organisation per customer.

The organisation's Zabbix data source logs in with a Zabbix API token that belongs to a
read-only account in the customer's own user group, so a customer can never see another
customer's hosts, even by editing a dashboard query.
"""

from __future__ import annotations

import json
import logging
import secrets as pysecrets
from pathlib import Path

from . import clients
from .clients.grafana import Grafana
from .common import Tenant
from .settings import settings

log = logging.getLogger("msp_glue.dashboards")

DASHBOARD_FILE = Path("/app/seed/grafana/customer-overview.json")


def enabled() -> bool:
    return bool(settings.grafana_url and settings.grafana_admin_password)


def ensure_customer_dashboards(tenant: Tenant) -> int | None:
    if not enabled():
        return None
    gf = Grafana(settings.grafana_url, settings.grafana_admin_user, settings.grafana_admin_password)
    org_id = gf.ensure_org(tenant.name)
    gf.ensure_admin_member(org_id)
    gf.enable_zabbix_app(org_id)
    datasource = gf.datasource(org_id, "Zabbix")
    if not datasource:
        token = _zabbix_readonly_token(tenant)
        datasource = gf.create_zabbix_datasource(org_id, "Zabbix", f"{settings.zabbix_url.rstrip('/')}/api_jsonrpc.php",
                                                 token)
        log.info("Grafana: Zabbix data source created for %s", tenant.code)
    if DASHBOARD_FILE.exists():
        text = DASHBOARD_FILE.read_text(encoding="utf-8").replace("${DS_ZABBIX}", datasource["uid"])
        dashboard = json.loads(text)
        dashboard["title"] = f"{tenant.name}: service overview"
        dashboard["uid"] = f"msp-{tenant.code}-overview"
        gf.import_dashboard(org_id, dashboard)
    return org_id


def _zabbix_readonly_token(tenant: Tenant) -> str:
    """A Zabbix account that can only read this customer's hosts, and a fresh API token for it."""
    z = clients.zabbix()
    zm = settings.mappings["zabbix"]
    group = z.call("usergroup.get", {"filter": {"name": [f"Customer {tenant.code} {zm['customer_readonly_suffix']}"]},
                                     "output": ["usrgrpid"]})
    if not group:
        raise RuntimeError(f"Zabbix read-only group for {tenant.code} missing; run the tenant sync first")
    role = z.ensure_api_reader_role()
    # "No access to the frontend" is a built-in group; the most restrictive frontend setting wins.
    no_ui = z.call("usergroup.get", {"filter": {"name": ["No access to the frontend"]}, "output": ["usrgrpid"]})
    groups = [{"usrgrpid": group[0]["usrgrpid"]}] + ([{"usrgrpid": no_ui[0]["usrgrpid"]}] if no_ui else [])
    login = f"grafana-{tenant.code}"
    user = z.call("user.get", {"filter": {"username": [login]}, "output": ["userid"]})
    if user:
        userid = user[0]["userid"]
        z.call("user.update", {"userid": userid, "roleid": role, "usrgrps": groups})
    else:
        userid = z.call("user.create", {
            "username": login, "name": "Grafana", "surname": tenant.code, "roleid": role,
            "passwd": pysecrets.token_urlsafe(24) + "aA1!", "usrgrps": groups,
        })["userids"][0]
    old = z.call("token.get", {"userids": [userid], "output": ["tokenid"]})
    if old:
        z.call("token.delete", [t["tokenid"] for t in old])
    tokenid = z.call("token.create", {"name": "grafana", "userid": userid})["tokenids"][0]
    return z.call("token.generate", [tokenid])[0]["token"]
