"""MSP Console: one web interface over Nautobot, Zabbix, GLPI, Semaphore, Forgejo and Grafana.

Runs as its own container (msp-console) from the msp-glue image, so a problem in the web interface
never stops webhook processing. Log-in goes through Keycloak (realm "msp"); changes are queued on
msp-glue's worker or made through the tools' APIs, and every one is written to the audit log.
"""
