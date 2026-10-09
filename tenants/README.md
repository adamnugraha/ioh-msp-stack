# Customer secrets

Device logins and SNMP credentials never go into Nautobot fields, Git or tickets. They live as
small text files on the platform host, one folder per customer, named after the customer code
(the Nautobot tenant custom field `msp_code`):

```
secrets/
  platform/
    forgejo_username        Git account used by Nautobot and Semaphore
    forgejo_token           its API token
  tenants/
    acme/
      device_username       SSH login used by Golden Config and Semaphore
      device_password
      device_enable         optional: enable secret (Cisco); if absent the password is used
      snmp_community        SNMP v2c community, or the three SNMP v3 files below
      snmpv3_user
      snmpv3_authpass
      snmpv3_privpass
```

The folder is mounted read-only into Nautobot (as `/run/msp-secrets`) and msp-glue. Nautobot
reads device logins through Secrets Groups that point at these files; msp-glue copies the SNMP
values into the customer's Zabbix settings template as secret macros, and the device login into
the customer's Semaphore project key.

## Creating a customer folder

```bash
umask 077
mkdir -p secrets/tenants/acme
printf '%s' 'netadmin'        > secrets/tenants/acme/device_username
printf '%s' 'the-password'    > secrets/tenants/acme/device_password
printf '%s' 'the-community'   > secrets/tenants/acme/snmp_community
chmod 755 secrets secrets/tenants secrets/tenants/acme
chmod 644 secrets/tenants/acme/*
```

The files must be readable by the container users (Nautobot runs as `nautobot`, msp-glue as
`glue`), which is why the files are world-readable inside a folder that only administrators of
the platform host can reach. Restrict SSH access to the platform host accordingly, and keep the
folder in the encrypted backup (scripts/backup.sh includes it).

Use `printf '%s'` rather than `echo` so no trailing newline ends up in a password.

## Rotating a secret

1. Change the password on the devices (Semaphore playbook or change window).
2. Overwrite the file.
3. Run `docker compose exec msp-glue mspctl tenant sync <code>` so Zabbix picks up new SNMP
   values. Golden Config reads the files at every run, so nothing else is needed. Semaphore
   keys are not overwritten automatically: delete the `device-login` key in the customer's
   Semaphore project, then run the tenant sync again to recreate it.

## Later: a secrets vault

When the customer count grows, move these files into OpenBao (open-source fork of HashiCorp
Vault, MPL 2.0). Nautobot reads it through the `nautobot-secrets-providers` app and Semaphore Pro
can use it directly; msp-glue would read it through the same API.
