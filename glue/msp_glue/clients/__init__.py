"""API clients for each tool, built from the runtime settings."""

from __future__ import annotations

from ..settings import settings
from .forgejo import Forgejo
from .glpi import GLPI
from .nautobot import Nautobot
from .semaphore import Semaphore
from .zabbix import Zabbix


def nautobot() -> Nautobot:
    return Nautobot(settings.nautobot_url, settings.nautobot_token)


def zabbix() -> Zabbix:
    return Zabbix(settings.zabbix_url, settings.zabbix_token)


def glpi() -> GLPI:
    """Return an unopened GLPI client; use it as a context manager: `with glpi() as g:`."""
    return GLPI(settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token)


def semaphore() -> Semaphore:
    return Semaphore(settings.semaphore_url, settings.semaphore_token)


def forgejo() -> Forgejo:
    return Forgejo(settings.forgejo_url, settings.forgejo_token)
