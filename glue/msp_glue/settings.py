"""Runtime settings, read from environment variables, plus the mapping file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    nautobot_url: str = field(default_factory=lambda: _env("NAUTOBOT_URL", "http://nautobot:8080"))
    nautobot_token: str = field(default_factory=lambda: _env("NAUTOBOT_TOKEN"))
    nautobot_webhook_secret: str = field(default_factory=lambda: _env("NAUTOBOT_WEBHOOK_SECRET"))

    zabbix_url: str = field(default_factory=lambda: _env("ZABBIX_URL", "http://zabbix-web:8080"))
    zabbix_public_url: str = field(default_factory=lambda: _env("ZABBIX_PUBLIC_URL"))
    zabbix_token: str = field(default_factory=lambda: _env("ZABBIX_API_TOKEN"))

    glpi_url: str = field(default_factory=lambda: _env("GLPI_URL", "http://glpi"))
    glpi_public_url: str = field(default_factory=lambda: _env("GLPI_PUBLIC_URL"))
    glpi_app_token: str = field(default_factory=lambda: _env("GLPI_APP_TOKEN"))
    glpi_user_token: str = field(default_factory=lambda: _env("GLPI_USER_TOKEN"))
    glpi_webhook_secret: str = field(default_factory=lambda: _env("GLPI_WEBHOOK_SECRET"))

    semaphore_url: str = field(default_factory=lambda: _env("SEMAPHORE_URL", "http://semaphore:3000"))
    semaphore_token: str = field(default_factory=lambda: _env("SEMAPHORE_API_TOKEN"))

    forgejo_url: str = field(default_factory=lambda: _env("FORGEJO_URL", "http://forgejo.platform.internal:3000"))
    forgejo_token: str = field(default_factory=lambda: _env("FORGEJO_API_TOKEN"))
    forgejo_org: str = field(default_factory=lambda: _env("FORGEJO_ORG", "msp"))

    grafana_url: str = field(default_factory=lambda: _env("GRAFANA_URL"))
    grafana_admin_user: str = field(default_factory=lambda: _env("GRAFANA_ADMIN_USER", "admin"))
    grafana_admin_password: str = field(default_factory=lambda: _env("GRAFANA_ADMIN_PASSWORD"))

    reconcile_interval: int = field(default_factory=lambda: int(_env("GLUE_RECONCILE_INTERVAL", "600") or 600))
    compliance_interval: int = field(default_factory=lambda: int(_env("GLUE_COMPLIANCE_INTERVAL", "3600") or 3600))
    secrets_dir: Path = field(default_factory=lambda: Path(_env("GLUE_SECRETS_DIR", "/run/msp-secrets/tenants")))
    hook_base: str = field(default_factory=lambda: _env("GLUE_PUBLIC_HOOK_BASE", "http://msp-glue.platform.internal"))
    mappings_file: Path = field(default_factory=lambda: Path(_env("GLUE_MAPPINGS", "/app/mappings.yaml")))
    dry_run: bool = field(default_factory=lambda: _env("GLUE_DRY_RUN", "false").lower() in ("1", "true", "yes"))
    lock_file: Path = field(default_factory=lambda: Path(_env("GLUE_LOCK_FILE", "/tmp/msp-glue.lock")))

    _mappings: dict | None = None

    @property
    def mappings(self) -> dict:
        if self._mappings is None:
            path = self.mappings_file
            if not path.exists():
                path = Path(__file__).resolve().parent.parent / "mappings.yaml"
            with open(path, encoding="utf-8") as handle:
                self._mappings = yaml.safe_load(handle) or {}
        return self._mappings

    def tenant_secret(self, tenant_slug: str, name: str, default: str = "") -> str:
        """Read one secret value for a customer, for example snmp_community.

        Secrets live as files: <secrets_dir>/<tenant_slug>/<name>
        The same folder is mounted read-only into Nautobot for device logins.
        """
        path = self.secrets_dir / tenant_slug / name
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return default


settings = Settings()
