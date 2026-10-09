"""msp-glue: integration service for the IOH open-source managed services platform.

Nautobot is the source of truth. msp-glue reads customers (tenants) and devices
from Nautobot and keeps Zabbix, GLPI, Semaphore and Forgejo in step with it.
It also receives webhooks (Nautobot, GLPI) and turns them into actions.
"""

__version__ = "1.0.0"
