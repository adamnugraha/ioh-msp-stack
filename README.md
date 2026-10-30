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
| Integration service and the MSP Console (this kit) | msp-glue | IOH-owned code |
| Customer dashboards (phase 3) | Grafana OSS 13 with the Zabbix plugin | AGPL 3.0 / Apache 2.0 |
| Single sign-on for staff | Keycloak 26 | Apache 2.0 |
| Chat alerts to Telegram, Microsoft Teams or e-mail | Versus Incident 1.4 | MIT |

Phases follow the build plan in the guide: the core tools go in first, then the reverse proxy, then
single sign-on, the MSP Console and chat alerts, and Grafana in phase 3. Tested versions: Nautobot
3.2.6, Golden Config 3.0.8, Zabbix 7.0.31, GLPI 11.0.11, Semaphore 2.19.14, Forgejo 12.0.4, Grafana
13.2.3 with Zabbix plugin 6.8.0, Keycloak 26.8.0, Versus Incident 1.4.13 (pinned in `.env.example`
and the compose files; the Python packages of msp-glue in `glue/constraints.txt`).

Nautobot is the master record. Engineers create a customer (tenant) and its devices in
Nautobot; msp-glue then creates everything else: the customer's GLPI entity and service level
rule, its Zabbix host groups, user group, settings template, proxy group and monthly
availability report, its Git backup repository and Golden Config scope, its Semaphore project,
and (phase 3) its Grafana organisation. Devices become Zabbix hosts and GLPI assets within
about 20 seconds. Zabbix problems open GLPI incidents in the right customer entity; approved GLPI changes
run Semaphore playbooks; Golden Config drift opens GLPI tickets; `mspctl user add` gives a
customer user access to its own entity in GLPI and its own Grafana organisation.

The MSP Console (`https://console.<domain>`) shows what every tool knows on one set of pages, with one
sign-in through Keycloak. From kit 1.2.0 its pages are a React application (dark, with white text)
that reads the console's own JSON interface (`/api/v1`):

* **Operations**: open problems of every customer, worst first, with their GLPI tickets and the notes
  engineers added; tickets due next; today's changes and maintenance windows; msp-glue's job queue;
  one row per customer across monitoring, tickets, changes, configuration, backups, automation and
  availability.
* **Customer pages**: devices; a network map from the cables and circuits recorded in Nautobot;
  problems and tickets; changes and maintenance; Semaphore runs; the customer's audit trail; downtime
  per day this month, exactly as its availability agreement in Zabbix counts it.
* **Device pages**: Zabbix charts (response time, loss, CPU, memory, disks, traffic per interface,
  from an hour to 30 days), interfaces with their cables and circuits, configuration backups from
  Forgejo, Golden Config's rule-by-rule results (secrets masked), tickets, runs and maintenance.
* **Service levels** for managers: availability against target, breaches, mean time to resolve,
  compliance and backups, this month or the two before, with a CSV export.
* **Actions**, each checked against the person's roles and written to the audit trail before it
  runs: acknowledge a problem (the note reaches the GLPI ticket), open a ticket, raise a GLPI change
  with its window, risk and back-out plan, plan Zabbix maintenance (regulated customers need a
  change), add a device to Nautobot, re-sync a customer, start a configuration check, and, with the
  automation role, run a Semaphore playbook for an open ticket or an approved change.
* **Investigate** (off until you give the console a model): the console reads Zabbix, Nautobot,
  GLPI, Golden Config and Semaphore with its own accounts, an OpenAI-compatible model you run or IOH
  has approved drafts the likely cause and next steps, and the engineer checks the draft before it
  goes to the ticket as a private note.

Staff sign in through Keycloak with three roles: manager, engineer and automation. From 1.2.0 the
same account also signs them in to Nautobot, Zabbix (SAML), Semaphore, Forgejo and Grafana once
`./scripts/install.sh sso-tools` has run, with the roles mapped to each tool's own groups; GLPI keeps
its own log-in, and every tool keeps its local accounts for the platform's service accounts and as the
way in when Keycloak is down.

Chat alerts: problems of severity High or worse on managed devices go to the NOC's Telegram group,
Teams channel or mailbox through Versus Incident, with the GLPI ticket, the Zabbix event and the
console page linked; acknowledgements and recoveries follow. GLPI stays the record of every incident.

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
  keycloak/                  single sign-on for staff (profile "sso")
  versus/                    chat alerts: settings and message templates (profile "chat")
  grafana/                   phase 3 (profile "grafana")
edge-collector/              Zabbix proxy for a customer site (pre-shared key, outbound only)
glue/                        msp-glue source: Python package, Dockerfile, mappings.yaml, unit tests;
                             msp_glue/console/ is the MSP Console (profile "console")
glue/console-ui/             the MSP Console front end from 1.2 on (React, TypeScript, shadcn/ui) and its
                             preview with sample data; see its README
branding/                    your logo for the console (the file stays out of Git; see its README)
.github/workflows/           image builds: build, scan with Trivy, push to Docker Hub
zabbix/media/                Zabbix webhooks "GLPI MSP" and "Versus chat" (JavaScript sources, importable YAML)
golden-config/               starter content of the shared Golden Config Git repository
semaphore/playbooks/         starter content of the shared playbook Git repository
grafana/                     customer dashboard template
scripts/                     prepare-server.sh (Docker, address pool, firewall), install.sh (the installer),
                             add-customer-secrets.sh, gen-env.sh, backup.sh, build-mediatype.py
tenants/README.md            how customer secrets are stored (in secrets/, never in Git)
lab/                         simulated devices, sample data, run-lab.sh and check.py (the sample
                             customers of install.sh samples come from here too)
```

## Try it in a lab first

Needs a Linux machine or VM with Docker (Compose v2.20+), 4 vCPU, 8 GB RAM, 40 GB disk, and
Internet access for the first build (Docker Hub, PyPI, grafana.com). It also runs on 2 vCPU; the first
start then takes longer (Nautobot's database migration alone took 10 minutes in testing).

```bash
./lab/run-lab.sh                       # builds, starts, creates two sample customers, runs 15 end-to-end checks
python3 lab/check.py golden_config     # run one check again (names are the functions in lab/check.py)
```

Behind a TLS-inspecting proxy: `export HTTPS_PROXY=... LAB_CA_FILE=/path/to/proxy-ca.crt` first.
The lab platform network uses 172.30.0.0/24; set `MSP_SUBNET` to change it.

## Images on Docker Hub

The two images the kit builds itself, and the console preview, are published to
`adityanugraha/ioh-msp-stack` by the workflow in `.github/workflows/images.yml`:

| Tag | What it is |
|---|---|
| `glue-1.2.0`, `glue-1.2` | msp-glue and the MSP Console, with the console's pages built in |
| `nautobot-3.2.6-gc3.0.8` | Nautobot with the Golden Config and Nornir apps |
| `console-preview` | the new console with made-up sample data; connects to nothing |

Every push builds and scans them with Trivy; they are pushed from `main`, or when the workflow is
started by hand (Actions, Images, Run workflow), once the repository has the secrets
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a Docker Hub access token with read and write access).

To use the published images instead of building on the server, pull and name them the way the
compose files expect, then start as usual:

```bash
docker pull adityanugraha/ioh-msp-stack:glue-1.2.0
docker tag adityanugraha/ioh-msp-stack:glue-1.2.0 msp/glue:1.2
docker pull adityanugraha/ioh-msp-stack:nautobot-3.2.6-gc3.0.8
docker tag adityanugraha/ioh-msp-stack:nautobot-3.2.6-gc3.0.8 msp/nautobot:3.2.6-gc3.0.8
```

The preview runs anywhere with Docker: `docker run -d -p 8080:8080 adityanugraha/ioh-msp-stack:console-preview`,
then open port 8080. To show your logo in it, add `-v /opt/ioh-msp-stack/branding:/usr/share/nginx/html/brand:ro`
(see `branding/README.md`).

## Install on a server

`scripts/install.sh` installs the platform on one Ubuntu 24.04 server in stages, checks each one,
and when something is wrong stops with the reason, the fix and the command that continues after
the fix. The interactive installation guide "IOH MSP Platform Install" walks through every command,
from an empty server to the first customer, with your own values filled in.

Each version of the kit is published as a zip in the `dist` folder of this repository, with its
SHA-256 checksum in `dist/SHA256SUMS`. On the server (`wget -O` works the same as `curl -o`):

```bash
curl -fL -o /tmp/ioh-msp-stack.zip https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist/ioh-msp-stack-v1.2.1.zip
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
./scripts/install.sh sso --admin-email you@ioh.co.id --name "Your Name"   # Keycloak and the first administrator
./scripts/install.sh console                       # the MSP Console on https://console.<domain>
./scripts/install.sh sso-tools                     # staff sign in to Nautobot, Zabbix, Semaphore, Forgejo, Grafana with Keycloak
./scripts/install.sh chat telegram                 # chat alerts (or: chat teams, chat email, chat test, chat off)
./scripts/install.sh grafana                       # phase 3: customer dashboards
./scripts/install.sh samples                       # optional: two made-up customers to practise on
```

`sso` starts Keycloak, creates the realm `msp` with the three roles and the console's client, and
with `--admin-email` the first administrator; it prints a one-time password that Keycloak replaces
at the first sign-in. `console` starts the MSP Console and proves that its sign-in goes to Keycloak.
Each needs its DNS name (or Cloudflare route): `sso.<domain>` and `console.<domain>`, like the tools.

`sso-tools` (after `sso`) gives Nautobot, Zabbix, Semaphore, Forgejo and Grafana (when it is on) a
Keycloak sign-in: a client per tool in Keycloak, then each tool's side. Name tools to do only those,
for example `./scripts/install.sh sso-tools nautobot zabbix`. What the roles become:

| Tool | manager | engineer | automation (with engineer) |
|---|---|---|---|
| Nautobot | group MSP managers: views everything | group MSP engineers: also changes the inventory and runs jobs | as engineer |
| Zabbix (SAML) | User role, group MSP managers (read-only) | Admin role, group MSP NOC engineers | as engineer |
| Semaphore | guest in every customer project | guest | task runner in every customer project |
| Forgejo | team managers (reads) | team engineers (reads) | team automation (writes playbooks and templates) |
| Grafana | Viewer in every organisation | Editor in every organisation | Editor |

Accounts appear at the first sign-in; someone without any of the three roles is refused (Grafana,
Zabbix) or sees nothing (the others). Semaphore has no role mapping of its own, so msp-glue adds staff
to every customer project within ten minutes of their first sign-in, and follows role changes the same
way. GLPI keeps its own log-in (its staff profiles and the customer portal). The tools' local accounts
(admin, the service accounts msp-glue uses) keep working, and are the way in when Keycloak is down.
If Keycloak's realm keys are ever rotated, run `./scripts/install.sh sso-tools nautobot zabbix` again.

Investigate in the console stays off until the console has a model to ask. Set the endpoint of an
OpenAI-compatible server (Ollama, vLLM, an IOH-approved hosted model) in `.env`, then run
`./scripts/install.sh console`:

```bash
CONSOLE_AI_URL=http://ollama:11434/v1     # here an Ollama container on the msp network; the evidence goes there only
CONSOLE_AI_MODEL=qwen3:14b
CONSOLE_AI_KEY=                            # when the endpoint needs a key
```

To show the operator's logo in the console's side bar, put `logo.svg` or `logo.png` in `branding/`
(see `branding/README.md`); the console serves it, and the file stays out of Git.
`chat` asks for the bot token and chat ID (Telegram), the workflow address (Teams) or the mail
server (e-mail), with hidden typing for secrets, checks them, sets up Zabbix and sends a test
message. Which severities go to chat, and the one-minute wait before the first message (a problem
that clears by itself within it never reaches the chat), are in the `chat` section of
`glue/mappings.yaml`.

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

From 1.0.x to 1.1.0, after unpacking: `./scripts/install.sh images` (builds msp-glue 1.1), then
`./scripts/install.sh sso --admin-email <you> --name "<your name>"`, `./scripts/install.sh console`
and, if you want chat alerts, `./scripts/install.sh chat telegram` (or teams, email). The missing
secrets are added to `.env` on the way; nothing else changes.

From 1.1.0 to 1.2.0, after backing up and unpacking:

```bash
./scripts/install.sh preflight      # 1.2 also needs https://registry.npmjs.org (the console's pages are built in the image)
./scripts/install.sh images         # builds msp-glue 1.2 with the new pages, and Nautobot with the sign-in settings
docker compose up -d                # every tool picks up its new settings
./scripts/install.sh glpi-api       # GLPI on the platform's time zone (it ran on UTC); tickets keep their real times
./scripts/install.sh console        # the new console; old bookmarks and chat links keep working
./scripts/install.sh sso-tools      # optional: staff sign in to the tools with Keycloak
./scripts/install.sh check
```

What changes for people: the console's pages look and work differently (same addresses); GLPI shows
times in the platform's time zone instead of UTC, and the business-hours calendar of the bronze tier
now really means 08:00 to 17:00 there; with `sso-tools`, the tools' log-in pages get a Keycloak
button. The console's audit trail and sessions carry over.

From 1.2.0 to 1.2.1, unpack and that is all: 1.2.1 adds `./scripts/install.sh samples` (see
"Sample customers for practice"), and its images are the same as 1.2.0's.

The guide's build tab lists the same work as manual steps, for anyone who wants to see or repeat a
single step by hand.

## Sample customers for practice

`./scripts/install.sh samples` puts the lab's two made-up customers on a platform server, so the
console has something to show before real customers arrive: PT ACME Retail Indonesia (`acme`) with a
router, a server and a planned switch, and PT Bank Contoh Tbk (`bankx`) with a router behind its own
edge collector. Their devices are small containers on the platform network, at addresses .201 to .204
of `MSP_SUBNET`: two simulated Cisco routers that answer SSH (one of them a little off the
configuration standard, so it gets a drift ticket) and a Linux server with Zabbix agent 2. The first run
builds two images, which needs pypi.org and dl-cdn.alpinelinux.org, and takes a few minutes.

```bash
./scripts/install.sh samples        # the two customers, their devices and the bank's edge collector
./scripts/install.sh samples off    # their devices Offline: no monitoring, alerts or tickets; history kept
```

A practice outage takes the sample server down and brings it back. Within about five minutes the
console shows the problem, GLPI has its ticket and, with chat alerts on, the NOC chat has the message:

```bash
docker stop msp-lab-devices-lab-acme-srv01-agent-1 msp-lab-devices-lab-acme-srv01-1
docker start msp-lab-devices-lab-acme-srv01-1 msp-lab-devices-lab-acme-srv01-agent-1
```

The simulated routers answer ping and SSH only, so Zabbix shows "No SNMP data collection" on both;
that warning is expected. The samples bring their own configuration standard (the config context
`sample-customers-standards`, for the two sample customers only), so the platform's `msp-standards`
stays as you set it. They count as customers everywhere, the monthly service report included. To
remove them for good, delete their devices, circuits, IP addresses, sites and the two tenants in
Nautobot, then run `docker compose -p msp-lab-devices -f lab/compose.devices.yaml down` and
`docker compose -p msp-lab-edge-bankx -f lab/.edge-bankx/compose.yaml down`. The tools keep their
records of the two customers (disabled Zabbix hosts, the GLPI entities, the Semaphore projects and
Grafana organisations), which an administrator can delete in each tool.

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
docker compose exec msp-glue mspctl staff add eko@ioh.co.id --name "Eko Prasetyo" --role engineer
docker compose exec msp-glue mspctl staff list            # staff accounts and their groups
docker compose exec msp-glue mspctl staff roles eko@ioh.co.id --role manager   # exactly these roles from now on
docker compose exec msp-glue mspctl staff reset eko@ioh.co.id     # new one-time password, sessions ended
docker compose exec msp-glue mspctl staff disable eko@ioh.co.id   # account off, sessions ended at once
docker compose exec msp-glue mspctl chat test             # one test message to the NOC chat
docker compose exec msp-glue mspctl sso sync              # Semaphore project memberships from the Keycloak roles, now
docker compose logs -f msp-glue                           # what msp-glue is doing
docker compose logs -f msp-console versus-incident        # the console and the chat alerts
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
* Docker hands out container addresses again after its own restart, so a lab device without a fixed
  address can end up with another container's old address while Nautobot still points at it. The lab
  devices now have fixed addresses (.201 to .204 of the platform network).
* Zabbix shows the name of the user who acknowledged a problem only to recipients who share a user
  group with that user; otherwise the follow-up says "Inaccessible user". The console acknowledges as
  svc-msp-glue, which `./scripts/install.sh console` adds to the NOC group, and names the engineer in
  the message.
* Keycloak behind a reverse proxy: `KC_HOSTNAME=https://sso.<domain>` with
  `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` gives browsers the public address and lets the console talk
  to `http://keycloak:8080` directly; the issuer stays the public one, so tokens check out either way.
* Versus Incident's message templates know a limited set of functions (`int` is missing, for example);
  the kit's templates use only what the tested 1.4.13 offers. Teams turns each line with a link into
  one clickable block, so the Teams template puts one link per line.
* The Nautobot base image (`networktocode/nautobot:3.2.6-py3.12`) lags Debian's security updates. The
  kit's Nautobot image applies them while it builds (best effort: the preflight warns when
  deb.debian.org cannot be reached). Trivy still reports four HIGH findings in msgpack, urllib3 and
  setuptools: these are copies that pip bundles inside itself, used only while pip runs, and they clear
  when pip updates its bundled copies.
* Versus Incident 1.4 releases add AI and enterprise features quickly. The kit uses only its alert
  path, with the agent, queues and on-call switched off; test a newer `VERSUS_IMAGE` in a lab first.
* **Zabbix keeps counting a problem that was open when its host was disabled** (Zabbix bug
  [ZBX-27917](https://support.zabbix.com/browse/ZBX-27917), confirmed for 7.0, not fixed yet). msp-glue
  disables, never deletes, the host of a device that is removed from Nautobot or no longer active, so a
  device that was down when it was retired keeps its customer's service "down" and lowers the monthly
  availability until the host is deleted. The console no longer shows those problems (as Zabbix's own
  Problems page), but the availability figure is Zabbix's. Until msp-glue handles it: after retiring a
  device that was down, delete its host in Zabbix (Data collection, Hosts) once its history is not
  needed; in the lab the customer's service recovered within a minute.
* The official GLPI image runs PHP in UTC, so GLPI showed and stored ticket times in UTC and read the
  "Business hours WIB" calendar as UTC hours. From 1.2.0 the installer switches on GLPI's time zone
  support and sets the platform's zone (`glpi-api`); GLPI keeps date-times as time stamps, so the times
  GLPI recorded itself (opened, solved, due) keep their real moments. Times someone typed into GLPI by
  hand while it showed UTC were read as UTC hours and now show 7 hours later (WIB); due times already
  set on open bronze tickets stay as they were computed. The console reads GLPI's own clock either way.
* Keycloak's default `role_list` scope sends one SAML `Role` attribute per role, and Zabbix refuses an
  assertion that repeats an attribute ("Found an Attribute element with duplicated Name"). The kit's
  Zabbix client sends every role in one attribute instead.
* Semaphore stops at start-up when `SEMAPHORE_OIDC_PROVIDERS` is set but empty; the kit's default is
  `{}` (no provider). Its manual endpoint settings are named `issuer`, `auth`, `token`, `userinfo`,
  `jwks` and `algorithms`.
