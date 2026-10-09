# Playbooks repository (shared by every customer)

Semaphore runs these playbooks in each customer's project against an inventory that msp-glue
generates from Nautobot. Inventory groups are the Nautobot device roles (router, switch, firewall,
server, ...); each host has `ansible_host`, `site` and the Ansible connection settings for its platform.

Rules for this repository:
* Read-only playbooks (diagnostics) may run on demand.
* Anything that changes a device runs only through an approved GLPI change (see the guide).
* Never put customer data or credentials here. Credentials come from the Semaphore project key
  `device-login`, which msp-glue fills from the customer's secrets folder.
