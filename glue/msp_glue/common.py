"""Helpers shared by the sync, onboarding and webhook modules."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .settings import settings

log = logging.getLogger("msp_glue")

CODE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,19}$")

# Custom fields msp-glue creates in Nautobot (key -> definition).
TENANT_FIELDS = {
    "msp_code": {"label": "MSP customer code", "type": "text",
                 "description": "Short lowercase code used in every tool, for example acme. Never change it after onboarding."},
    "msp_service_tier": {"label": "MSP service tier", "type": "select", "choices": ["gold", "silver", "bronze"],
                         "default": "silver"},
    "msp_customer_id": {"label": "IOH customer account number", "type": "text"},
    "msp_regulated": {"label": "Regulated customer (bank, government)", "type": "boolean", "default": False},
    "msp_proxy_group": {"label": "Zabbix proxy group", "type": "text",
                        "description": "Leave empty to use the central proxies."},
    "msp_snmp_version": {"label": "SNMP version", "type": "select", "choices": ["2", "3"], "default": "2"},
    "msp_contact_email": {"label": "Customer contact e-mail", "type": "text"},
    "msp_glpi_entity_id": {"label": "GLPI entity id", "type": "integer", "managed": True},
    "msp_zabbix_hostgroup_id": {"label": "Zabbix host group id", "type": "text", "managed": True},
    "msp_semaphore_project_id": {"label": "Semaphore project id", "type": "integer", "managed": True},
    "msp_provisioning_status": {"label": "MSP provisioning status", "type": "select",
                                "choices": ["pending", "provisioned", "error"], "default": "pending", "managed": True},
    "msp_provisioning_message": {"label": "MSP provisioning message", "type": "text", "managed": True},
}

DEVICE_FIELDS = {
    "msp_monitoring": {"label": "Monitor in Zabbix", "type": "boolean", "default": True},
    "msp_zabbix_hostid": {"label": "Zabbix host id", "type": "text", "managed": True},
    "msp_glpi_asset_id": {"label": "GLPI asset id", "type": "integer", "managed": True},
    "msp_glpi_itemtype": {"label": "GLPI asset type", "type": "text", "managed": True},
    "msp_drift_ticket_id": {"label": "Open drift ticket", "type": "integer", "managed": True},
}


@dataclass
class Tenant:
    id: str
    name: str
    code: str
    tier: str = "silver"
    customer_id: str = ""
    regulated: bool = False
    proxy_group: str = ""
    snmp_version: str = "2"
    contact_email: str = ""
    glpi_entity_id: int | None = None
    zabbix_hostgroup_id: str | None = None
    semaphore_project_id: int | None = None
    custom_fields: dict = field(default_factory=dict)

    @classmethod
    def from_nautobot(cls, raw: dict) -> "Tenant":
        cf = raw.get("custom_fields") or {}
        return cls(
            id=raw["id"],
            name=raw["name"],
            code=(cf.get("msp_code") or "").strip().lower(),
            tier=(cf.get("msp_service_tier") or "silver"),
            customer_id=cf.get("msp_customer_id") or "",
            regulated=bool(cf.get("msp_regulated")),
            proxy_group=(cf.get("msp_proxy_group") or "").strip(),
            snmp_version=str(cf.get("msp_snmp_version") or "2"),
            contact_email=cf.get("msp_contact_email") or "",
            glpi_entity_id=cf.get("msp_glpi_entity_id"),
            zabbix_hostgroup_id=cf.get("msp_zabbix_hostgroup_id"),
            semaphore_project_id=cf.get("msp_semaphore_project_id"),
            custom_fields=cf,
        )

    @property
    def valid(self) -> bool:
        return bool(CODE_PATTERN.match(self.code))

    @property
    def zabbix_group(self) -> str:
        return f"{settings.mappings['zabbix']['customer_group_root']}/{self.name}"

    @property
    def macro_template(self) -> str:
        return f"Customer {self.code} settings"

    @property
    def proxy_group_name(self) -> str:
        return self.proxy_group or settings.mappings["zabbix"]["default_proxy_group"]


def zabbix_safe(text: str) -> str:
    """Zabbix technical host names allow letters, digits, spaces, dots, dashes and underscores."""
    return re.sub(r"[^A-Za-z0-9 ._-]", "_", text).strip()[:128]


def host_ip(primary_ip: dict | None) -> str:
    if not primary_ip:
        return ""
    return (primary_ip.get("host") or primary_ip.get("address", "")).split("/")[0]
