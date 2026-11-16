# Installing on a server

The full reference for `scripts/install.sh`: every stage, every setting, and what each one changes. The [README](../README.md#install-on-a-server) has the short path; [environments/](../environments/README.md) covers Google Cloud, AWS, a VPS and the automatic install.

`scripts/install.sh` installs the platform on one Ubuntu 24.04 server in stages, checks each one,
and when something is wrong stops with the reason, the fix and the command that continues after
the fix. The interactive installation guide "IOH MSP Platform Install" walks through every command,
from an empty server to the first customer, with your own values filled in.

This page describes a server in IOH's private cloud. For a trial on Google Cloud, AWS or a VPS
from any provider, follow [environments/](../environments/README.md) instead: a script there creates the
server in the cloud, and the steps use the same installer with a few other values (no web proxy, the
cloud's firewall, and a Cloudflare Tunnel or direct access as the way in). There, one settings file and one command can
also do everything at once (`scripts/autoinstall.sh`, "Automatic install"), which makes a rebuild two
commands.

Each version of the kit is published as a zip in the `dist` folder of this repository, with its
SHA-256 checksum in `dist/SHA256SUMS`. On the server (`wget -O` works the same as `curl -o`):

```bash
curl -fL -o /tmp/ioh-msp-stack.zip https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist/ioh-msp-stack-v1.6.0.zip
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
./scripts/install.sh gate                          # direct access: the sign-in gate in front of the engineers' tools
./scripts/install.sh chat telegram                 # chat alerts (or: chat teams, chat email, chat test, chat off)
./scripts/install.sh grafana                       # phase 3: customer dashboards
./scripts/install.sh samples                       # optional: two made-up customers to practise on
```

Two settings decide where the published ports listen: `--web-listen` for Caddy's 80 and 443 and
`--collectors-listen` for Zabbix's 10051 (collectors at customer sites). Both default to `0.0.0.0`,
every network, as IOH's network needs. `127.0.0.1` keeps a port on the server itself: for the web
ports when a Cloudflare Tunnel on the server is the way in, and for 10051 when no collector connects
from outside. Docker's published ports bypass the server's firewall (ufw), so these settings are what
closes them. On a running platform, `docker compose up -d proxy zabbix-server` applies a change.
`--way-in ioh`, `cloudflare` or `direct` sets both, `ADMIN_NETWORKS` and the certificates the way each
way in needs them; [environments/direct-access.md](../environments/direct-access.md) explains the third,
for a server that browsers reach straight from the Internet (Let's Encrypt and the sign-in gate).

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

## Images on Docker Hub instead of building

The two images the kit builds itself, and the console preview, are published to
`adityanugraha/ioh-msp-stack` by the workflow in `.github/workflows/images.yml`:

| Tag | What it is |
|---|---|
| `glue-1.3.0`, `glue-1.3` | msp-glue and the MSP Console, with the console's pages built in (kit 1.6.0) |
| `nautobot-3.2.6-gc3.0.8` | Nautobot with the Golden Config and Nornir apps |
| `console-preview` | the new console with made-up sample data; connects to nothing |

Every push builds and scans them with Trivy; they are pushed from `main`, or when the workflow is
started by hand (Actions, Images, Run workflow), once the repository has the secrets
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a Docker Hub access token with read and write access).

To use the published images instead of building on the server, pull and name them the way the
compose files expect, then start as usual:

```bash
docker pull adityanugraha/ioh-msp-stack:glue-1.3.0
docker tag adityanugraha/ioh-msp-stack:glue-1.3.0 msp/glue:1.3
docker pull adityanugraha/ioh-msp-stack:nautobot-3.2.6-gc3.0.8
docker tag adityanugraha/ioh-msp-stack:nautobot-3.2.6-gc3.0.8 msp/nautobot:3.2.6-gc3.0.8
```

The preview runs anywhere with Docker: `docker run -d -p 8080:8080 adityanugraha/ioh-msp-stack:console-preview`,
then open port 8080. To show your logo in it, add `-v /opt/ioh-msp-stack/branding:/usr/share/nginx/html/brand:ro`
(see `branding/README.md`).

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

[Back to the README](../README.md)
