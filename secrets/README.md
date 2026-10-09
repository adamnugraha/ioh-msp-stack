# secrets/

Device logins, SNMP credentials and platform Git credentials live here as small text files, one
folder per customer under `tenants/` and the platform's own under `platform/`. The folder is mounted
read-only into Nautobot and msp-glue. It is excluded from Git (see `.gitignore`) and included in the
nightly backup. See `tenants/README.md` in the kit root for the file names and commands.
