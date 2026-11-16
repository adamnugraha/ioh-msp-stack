# Everyday commands

```bash
docker compose exec msp-glue mspctl check                 # can msp-glue reach every tool?
docker compose exec msp-glue mspctl tenant list           # customers and their provisioning status
docker compose exec msp-glue mspctl tenant sync acme      # provision or repair one customer
docker compose exec msp-glue mspctl -v device sync acme   # push one customer's devices again, with details
docker compose exec msp-glue mspctl --dry-run device sync acme   # only report what would change
docker compose exec msp-glue mspctl proxy add acme proxy-acme-01   # register a site proxy (again = new key)
docker compose exec msp-glue mspctl paths list acme       # a customer's path test targets, collectors and Zabbix hosts
docker compose exec msp-glue mspctl paths sync acme       # create or repair them now (the reconcile does it every 10 minutes)
docker compose exec msp-glue mspctl capacity acme --no-tickets   # link capacity forecast now, without opening tickets
docker compose exec msp-glue mspctl bootstrap platform    # re-apply the shared baseline (keeps webhook settings)
docker compose exec msp-glue mspctl user add acme budi@acme.example --name "Budi Santoso" --grafana
docker compose exec msp-glue mspctl user disable acme budi@acme.example
docker compose exec msp-glue mspctl compliance            # refresh configuration-drift tickets
docker compose exec msp-glue mspctl staff add eko@ioh.co.id --name "Eko Prasetyo" --role engineer
docker compose exec msp-glue mspctl staff list            # staff accounts and their groups
docker compose exec msp-glue mspctl staff roles eko@ioh.co.id --role manager   # exactly these roles from now on
docker compose exec msp-glue mspctl staff reset eko@ioh.co.id     # new one-time password, sessions ended
docker compose exec msp-glue mspctl staff disable eko@ioh.co.id   # account off, sessions ended at once
docker compose exec msp-glue mspctl chat test             # one test message to the NOC chat
docker compose exec msp-glue mspctl sso sync              # Semaphore project memberships from the Keycloak roles, now
docker compose logs -f msp-glue                           # what msp-glue is doing
docker compose logs -f msp-console versus-incident        # the console and the chat alerts
docker compose exec ollama ollama list                    # the language model on this server (with ./scripts/install.sh ai)
./scripts/add-customer-secrets.sh acme                    # device log-in and SNMP secrets of a new customer
./scripts/install.sh status                               # installation stages done, certificates in use
sudo ./scripts/backup.sh                                  # nightly backup (cron, as root)
```

Staff roles: `manager` (service view, read-only operations), `engineer` (operations and the
console's actions), `automation` (engineer plus playbooks) and `admin` (all three). A disabled
account, or a role taken away with `staff roles`, takes effect in the console within five minutes,
when the console next checks with Keycloak. The console starts a playbook only when its Semaphore
template allows the device limit to be set per run (`allow_override_limit`, on in every template the
kit creates); without it Semaphore would run the playbook on all of the customer's devices.

After editing `glue/mappings.yaml` or the msp-glue code, rebuild or restart it:
`docker compose build msp-glue && docker compose up -d msp-glue msp-console`.

[Back to the README](../README.md)
