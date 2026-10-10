# IOH open-source managed services platform: build and integration kit

This kit builds a multi-customer platform for managed infrastructure and network services
from open-source tools only:

| Role | Tool | Licence |
|---|---|---|
| Network source of truth, configuration backup and compliance | Nautobot 3.2 with Golden Config 3.0 | Apache 2.0 |
| Monitoring, alerting and monthly availability per customer | Zabbix 7.0 LTS (8.0 LTS when released) | AGPL 3.0 |
| Service desk, customer portal, service levels, asset register | GLPI 11 | GPL 3.0 |
| Automation (Ansible and OpenTofu runs) | Semaphore UI Community 2.19 | MIT |
| Git server for backups, templates and playbooks | Forgejo 12 | GPL 3.0 or later |
| Integration service (this kit) | msp-glue | IOH-owned code |
| Customer dashboards (phase 3) | Grafana OSS 13 with the Zabbix plugin | AGPL 3.0 / Apache 2.0 |
| Single sign-on (phase 3) | Keycloak 26 | Apache 2.0 |

Phases follow the build plan in the guide: the core tools go in first, Grafana and Keycloak in
phase 3. Tested versions: Nautobot 3.2.6, Golden Config 3.0.8, Zabbix 7.0.31, GLPI 11.0.11,
Semaphore 2.19.14, Forgejo 12.0.4, Grafana 13.2.3 with Zabbix plugin 6.8.0 (pinned in `.env.example`
and the compose files).

Nautobot is the master record. Engineers create a customer (tenant) and its devices in
Nautobot; msp-glue then creates everything else: the customer's GLPI entity and service level
rule, its Zabbix host groups, user group, settings template, proxy group and monthly
availability report, its Git backup repository and Golden Config scope, its Semaphore project,
and (phase 3) its Grafana organisation. Devices become Zabbix hosts and GLPI assets within
about 20 seconds. Zabbix problems open GLPI incidents in the right customer entity; approved GLPI changes
run Semaphore playbooks; Golden Config drift opens GLPI tickets; `mspctl user add` gives a
customer user access to its own entity in GLPI and its own Grafana organisation.

The full guide (architecture, every step, every integration, operations) is the document
"IOH open-source managed services platform" that comes with this kit.

## Folder map

```
compose.yaml                 single-host deployment (includes the files under components/)
.env.example                 every setting and secret; copy with scripts/gen-env.sh
components/                  one compose file per tool, so tools can be split across VMs
  postgres/                  PostgreSQL, MariaDB (GLPI) and Valkey
  nautobot/                  image with Golden Config, nautobot_config.py
  zabbix/ glpi/ semaphore/ forgejo/ glue/
  proxy/                     Caddy reverse proxy with TLS and network access rules (profile "proxy")
  grafana/ keycloak/         phase 3 (profiles "grafana" and "sso")
edge-collector/              Zabbix proxy for a customer site (pre-shared key, outbound only)
glue/                        msp-glue source: Python package, Dockerfile, mappings.yaml, unit tests
zabbix/media/                Zabbix webhook "GLPI MSP" (JavaScript source and importable YAML)
golden-config/               starter content of the shared Golden Config Git repository
semaphore/playbooks/         starter content of the shared playbook Git repository
grafana/                     customer dashboard template
scripts/                     prepare-server.sh (Docker, address pool, firewall), install.sh (the installer),
                             add-customer-secrets.sh, gen-env.sh, backup.sh, build-mediatype.py
tenants/README.md            how customer secrets are stored (in secrets/, never in Git)
lab/                         simulated devices, sample data, run-lab.sh and check.py
```

## Try it in a lab first

Needs a Linux machine or VM with Docker (Compose v2.20+), 4 vCPU, 8 GB RAM, 40 GB disk, and
Internet access for the first build (Docker Hub, PyPI, grafana.com). It also runs on 2 vCPU; the first
start then takes longer (Nautobot's database migration alone took 10 minutes in testing).

```bash
./lab/run-lab.sh                       # builds, starts, creates two sample customers, runs 12 end-to-end checks
python3 lab/check.py golden_config     # run one check again (names are the functions in lab/check.py)
```

Behind a TLS-inspecting proxy: `export HTTPS_PROXY=... LAB_CA_FILE=/path/to/proxy-ca.crt` first.
The lab platform network uses 172.30.0.0/24; set `MSP_SUBNET` to change it.

## Install on a server

`scripts/install.sh` installs the platform on one Ubuntu 24.04 server in stages, checks each one,
and when something is wrong stops with the reason, the fix and the command that continues after
the fix. The interactive installation guide "IOH MSP Platform Install" walks through every command,
from an empty server to the first customer, with your own values filled in.

Each version of the kit is published as a zip in the `dist` folder of this repository, with its
SHA-256 checksum in `dist/SHA256SUMS`. On the server (`wget -O` works the same as `curl -o`):

```bash
curl -fL -o /tmp/ioh-msp-stack.zip https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist/ioh-msp-stack-v1.0.2.zip
sha256sum /tmp/ioh-msp-stack.zip      # must equal the line for this file in dist/SHA256SUMS
command -v unzip >/dev/null || (sudo apt-get update -qq && sudo apt-get install -y unzip)
sudo mkdir -p /opt/ioh-msp-stack && sudo chown "$(id -un):" /opt/ioh-msp-stack
rm -rf /tmp/kit && mkdir /tmp/kit && unzip -q /tmp/ioh-msp-stack.zip -d /tmp/kit
cp -a /tmp/kit/*/. /opt/ioh-msp-stack/ && chmod +x /opt/ioh-msp-stack/scripts/*.sh
```

`git clone https://github.com/adamnugraha/ioh-msp-stack /opt/ioh-msp-stack` gets the same files
with git instead.

Use one account for every step, from unpacking onwards: a personal account with sudo rights
(recommended) or root. The installer runs as the owner of the kit folder, without sudo, and stops
with the fix when it is started another way. Only `prepare-server.sh` and `backup.sh` need sudo.

Prepare the server once: Ubuntu updates, Docker with Compose, Docker's address pool and log
rotation, and the firewall (add `--proxy` and `--proxy-ca` behind IOH's web proxy). Then log out
and in again, so your account's docker group membership applies (with tmux, run
`tmux kill-server` first, because tmux keeps the groups it started with):

```bash
cd /opt/ioh-msp-stack
sudo ./scripts/prepare-server.sh --admin-networks "10.10.0.0/16 10.20.0.0/16" --pool 172.31.0.0/16 --tz Asia/Jakarta
```

Then, from `/opt/ioh-msp-stack` and without sudo:

```bash
./scripts/install.sh preflight                     # check the server; changes nothing
./scripts/install.sh settings --domain msp.ioh.internal --subnet 172.30.0.0/24 \
    --admin-networks "10.10.0.0/16 10.20.0.0/16" --tz Asia/Jakarta --email noc@example.co.id
./scripts/install.sh                               # every remaining stage, in order (20 to 45 minutes)
./scripts/install.sh status                        # which stages are done
./scripts/install.sh proxy                         # HTTPS on https://<tool>.<domain> (needs the DNS names)
./scripts/install.sh certs ~/fullchain.pem ~/privkey.pem   # IOH certificates instead of Caddy's own
./scripts/install.sh grafana                       # phase 3: customer dashboards
```

Core stages: preflight, settings, network, images, start, glpi-api, forgejo-admin, tokens, platform,
glue, harden (new random passwords for Zabbix `Admin` and GLPI `glpi`, GLPI demo accounts switched
off), check. Every password the platform uses is in `.env`. Behind a web proxy, export `HTTPS_PROXY`
(and `CA_FILE` for a TLS-inspecting proxy) before running the installer.

`certs` checks the certificate and key before it changes anything (key without pass phrase, key
belongs to the certificate, every name covered, not expired), then proves every name serves the new
certificate and puts the previous certificates back if not. The choice is kept in `.env`
(`CADDY_TLS`), so kit updates never undo it. `./scripts/install.sh certs internal` goes back to
Caddy's own certificate authority.

To update the kit later: back up, download the new version's zip from `dist`, unzip it over the
folder the same way (your `.env`, secrets and certificates are not in the zip, so they stay), then run
`./scripts/install.sh images`, `docker compose up -d` and `./scripts/install.sh check`.

The guide's build tab lists the same work as manual steps, for anyone who wants to see or repeat a
single step by hand.

## Everyday commands

```bash
docker compose exec msp-glue mspctl check                 # can msp-glue reach every tool?
docker compose exec msp-glue mspctl tenant list           # customers and their provisioning status
docker compose exec msp-glue mspctl tenant sync acme      # provision or repair one customer
docker compose exec msp-glue mspctl -v device sync acme   # push one customer's devices again, with details
docker compose exec msp-glue mspctl --dry-run device sync acme   # only report what would change
docker compose exec msp-glue mspctl proxy add acme proxy-acme-01   # register a site proxy (again = new key)
docker compose exec msp-glue mspctl bootstrap platform    # re-apply the shared baseline (keeps webhook settings)
docker compose exec msp-glue mspctl user add acme budi@acme.example --name "Budi Santoso" --grafana
docker compose exec msp-glue mspctl user disable acme budi@acme.example
docker compose exec msp-glue mspctl compliance            # refresh configuration-drift tickets
docker compose logs -f msp-glue                           # what msp-glue is doing
./scripts/add-customer-secrets.sh acme                    # device log-in and SNMP secrets of a new customer
./scripts/install.sh status                               # installation stages done, certificates in use
sudo ./scripts/backup.sh                                  # nightly backup (cron, as root)
```

After editing `glue/mappings.yaml` or the msp-glue code, rebuild or restart it:
`docker compose build msp-glue && docker compose up -d msp-glue`.

## Known issues found during testing (October 2026)

* scrapli 2026.10.3 (released 3 October 2026) breaks nornir-scrapli and every Golden Config job.
  `components/nautobot/requirements.txt` pins `scrapli<2026.10`.
* Golden Config installs a "Default Settings" whose scope is every device. A job run for one customer
  then also tries that customer's servers and planned devices and fails (E3018). The bootstrap removes
  it once; customer settings cover active network devices only.
* Golden Config's multi-device job hides errors unless "fail job on task failure" is ticked;
  msp-glue's nightly schedules tick it and run one schedule per customer.
* Nautobot 3.2 reports the deletion of a device that has a primary IP address as an "updated" event.
  msp-glue treats a device that no longer exists as deleted and disables its Zabbix host.
* Zabbix 7.0 does not resolve `{EVENT.UPDATE.NSEVERITY}` in webhook parameters; the GLPI MSP
  webhook uses `{EVENT.NSEVERITY}` instead.
* Semaphore ignores the host limit sent with a task unless the template allows it, and re-enables the
  Ansible Galaxy install when a template allows the task to override it. msp-glue sets
  `allow_override_limit` on and `allow_override_skip_galaxy_install` off on every template.
* GLPI's API only hashes a password sent together with an identical `password2`; msp-glue always
  sends both.
* A GLPI API session does not see an entity created during that session, and GLPI then refuses to add
  groups or rules to it ("You don't have permission to perform this action"). msp-glue refreshes the
  session's active entities right after it creates a customer entity.
* GLPI refuses webhook URLs with a port number and Nautobot refuses host names without a dot,
  so msp-glue listens on port 80 under the alias `msp-glue.platform.internal`.
* Grafana 13.2 cannot delete an organisation through its API, and without automatic organisation
  assignment it creates a personal organisation for every new user. The kit assigns new users to
  "Main Org." and msp-glue moves customer users into their own organisation.
* Docker's default address pools (172.17.0.0/16 and up, 192.168.0.0/16) can overlap networks you
  manage and hide them behind a local route. Create the `msp` network with an explicit subnet and set
  `default-address-pools` in `/etc/docker/daemon.json` to a range no customer uses.
