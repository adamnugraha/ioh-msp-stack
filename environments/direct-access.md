# Direct access: the way in without Cloudflare

On Google Cloud, AWS or a VPS, browsers can reach the platform straight from the Internet instead of
through a Cloudflare Tunnel. The server then has a fixed public address with ports 80 and 443 open,
Caddy gets certificates from Let's Encrypt, and a sign-in gate of the kit's own stands in front of the
engineers' tools, where Cloudflare Access stands with a tunnel. Any domain whose DNS you manage works;
nothing on Cloudflare is needed.

## What stands in front of each name

The same split as Cloudflare Access in [cloudflare-tunnel.md](cloudflare-tunnel.md), section 3:

| Name | For | In front of it |
|---|---|---|
| `console` | the MSP Console | the sign-in gate |
| `nautobot`, `zabbix`, `semaphore`, `git`, `glue` | the engineering tools | the sign-in gate |
| `sso` | staff sign-in (Keycloak) | the gate for `/admin` and the master realm only |
| `glpi` | service desk and customer portal | the gate for the paths `/apirest.php` and `/api.php` only |
| `grafana` | customer dashboards, when they are on | nothing more: Grafana's own sign-in |

The sign-in gate is oauth2-proxy, an open-source component the kit runs next to Caddy
(`components/gate`). Caddy asks it about every request to those names. A browser that has not signed in
goes to Keycloak's sign-in page, with the same staff account as the MSP Console. The gate lets through
accounts with the role msp-manager, msp-engineer or msp-automation and answers 403 to every other
account. One sign-in covers every name for 8 hours; the tools then sign the person in through Keycloak
as usual (after `sso-tools`), without a second password. Customers reach GLPI's portal and Grafana as
before, without the gate.

## 1. The domain and its DNS record

Every tool gets a name directly under the domain: `console.<domain>`, `sso.<domain>` and seven more.
One wildcard record at your DNS provider covers them all:

```
*.<domain>   A   <the server's fixed address>
```

On Cloudflare's DNS, set the record to DNS only (the grey cloud): with Cloudflare's proxy on, Caddy
never sees the real visitors and Let's Encrypt cannot check the names. A domain that served a Cloudflare
Tunnel before has the tunnel's records (CNAME records to `<id>.cfargotunnel.com`): delete them first,
with the tunnel's published routes.

Create the record as soon as the create script shows the address. The automatic install waits for it
before Caddy asks Let's Encrypt for certificates; by hand, `./scripts/install.sh dns` shows each name and
where it points. Each name points at one platform: a second platform needs a second domain.

## 2. The fixed address and the open ports

| Environment | Fixed address | Ports 80 and 443 |
|---|---|---|
| Google Cloud | `create.sh --way-in direct` reserves the external address `msp-test-01-ip` | firewall rule `msp-net-web`, from anywhere |
| AWS | `create.sh --way-in direct` allocates the Elastic IP `msp-test-01-ip` | the security group `msp-test-01-sg`, from anywhere |
| A VPS | the address the provider gives the server | the provider's firewall, if it has one: allow both from anywhere |

With `--autoinstall`, the create scripts read `WAY_IN=direct` from the settings file, so `--way-in` is
only needed without one. Port 80 stays open to everyone: Let's Encrypt checks each name there, and Caddy
sends browsers on to HTTPS. SSH and port 10051 (collectors) stay as they were. The delete scripts keep
the address for a rebuild, so the DNS record stays right; `--release-address` releases it when the trial
is over.

## 3. The kit's settings

**Automatic install:** `WAY_IN=direct` in the settings file, and no tunnel token
([autoinstall.conf.example](autoinstall.conf.example)). Let's Encrypt's account is `NOC_EMAIL`, or else
`STAFF_EMAIL`.

**By hand**, after `prepare-server.sh` as the environment's README shows:

```bash
./scripts/install.sh settings --domain <your domain> --subnet 172.30.0.0/24 --way-in direct \
    --letsencrypt-email <your e-mail> --tz Asia/Jakarta --email <your e-mail>
```

`--way-in direct` sets everything that goes with it:

- ports 80 and 443 listen on every network, port 10051 on this server only (`--collectors-listen
  0.0.0.0` opens it, once a collector at a customer site needs it);
- `ADMIN_NETWORKS` lets every address through, and the gate decides. With `--admin-networks "<your
  office ranges>"`, only those networks reach the engineering tools at all, the gate still in front;
- certificates from Let's Encrypt, with the `--letsencrypt-email` account;
- the gate closed until its stage runs: the engineering tools answer 503 until then.

Then, in this order:

```bash
tmux new -A -s msp            # a dropped connection does not stop the installer
./scripts/install.sh          # 20 to 45 minutes
./scripts/install.sh sso --admin-email <your e-mail> --name "<your name>"
./scripts/install.sh gate     # the sign-in gate, before anything answers on the Internet
./scripts/install.sh dns      # does every name point at this server?
./scripts/install.sh proxy    # HTTPS, with Let's Encrypt's certificates (1 to 3 minutes)
./scripts/install.sh console
./scripts/install.sh sso-tools
```

The proxy stage checks the DNS records again before it starts Caddy, waits until every name has its
certificate, then follows the sign-in the way a browser would: from a tool to the gate, from the gate to
Keycloak's sign-in page. Caddy renews the certificates by itself.

## 4. Check

From a phone on mobile data, open `https://console.<domain>`: Keycloak's sign-in page appears first (the
gate), then the console. `https://glpi.<domain>` opens GLPI's own log-in page, without the gate.
`./scripts/install.sh status` shows the way in, the gate and where the certificates come from.

To sign out completely on a shared PC (the gate, then Keycloak itself), open:
`https://console.<domain>/oauth2/sign_out?rd=https%3A%2F%2Fsso.<domain>%2Frealms%2Fmsp%2Fprotocol%2Fopenid-connect%2Flogout`

| You see | Cause | Fix |
|---|---|---|
| `./scripts/install.sh dns`: "no address yet" | the record is missing, or the server still remembers the answer from before it existed | create the record, wait a few minutes, `sudo resolvectl flush-caches`, then the same command again |
| "Cloudflare's proxy is on for it" | the record has Cloudflare's proxy on (orange cloud) | set it to DNS only |
| The proxy stage: "no certificate after 10 minutes" | Let's Encrypt cannot reach port 80 or 443, or a name points elsewhere | open both ports to everyone (the cloud firewall or the provider's panel), check with `./scripts/install.sh dns`, then `./scripts/install.sh proxy` again |
| "Staff sign-in is not ready yet." (503) | the gate stage has not run | `./scripts/install.sh gate` |
| 403 Forbidden after the sign-in | the account has no MSP role | `docker compose exec msp-glue mspctl staff roles <e-mail> --role engineer` (or manager, automation, admin) |
| "This service is only available from the IOH network." | `ADMIN_NETWORKS` lists networks, and yours is not one of them | `./scripts/install.sh settings --way-in direct` lets every address through again, or add your range with `--admin-networks` |

## Rebuilds and Let's Encrypt's limits

A rebuild is the environment's delete script, then the same create command with the same settings file.
The fixed address stays, so the DNS record stays right, and the new server asks for nine new
certificates, one per name. Let's Encrypt gives the same name at most 5 certificates in 7 days, and one
registered domain at most 50, so plan on about five rebuilds a week with one domain. Past that, Caddy
tries ZeroSSL with the same e-mail address, which is free and trusted by browsers too; a second domain
for test rebuilds avoids the question.

## Changing the way in

**From direct to a Cloudflare Tunnel:** prepare the Cloudflare side ([cloudflare-tunnel.md](cloudflare-tunnel.md),
sections 3 and 4) and delete the A record. On the server, `./scripts/install.sh settings --way-in cloudflare`
and `docker compose up -d proxy zabbix-server`. The gate may stay on behind Cloudflare Access, or go:
`./scripts/install.sh gate off`. On Google Cloud or AWS, `create.sh --way-in cloudflare` closes ports 80
and 443 again; the delete script with `--release-address` releases the fixed address.

**From a tunnel to direct:** keep the fixed address and open the ports with `create.sh --way-in direct`
(on a VPS, the provider's firewall). Delete the tunnel's DNS records and routes, create the A record, then
on the server: `./scripts/install.sh settings --way-in direct --letsencrypt-email <your e-mail>`,
`./scripts/install.sh gate`, `./scripts/install.sh proxy`, and stop the tunnel's connector
(`sudo systemctl disable --now cloudflared`).

## Direct access next to the tunnel

| | Cloudflare Tunnel | Direct access |
|---|---|---|
| Open ports on the server | none: the tunnel connects out | 80 and 443 |
| Who asks engineers who they are | Cloudflare Access (an e-mail code) | the kit's sign-in gate (the staff account in Keycloak) |
| Certificates browsers see | Cloudflare's | Let's Encrypt's |
| Set up once | a domain on Cloudflare, Zero Trust, the tunnel and its routes | one DNS record |
| A rebuild keeps | the tunnel, through its token in the settings file | the fixed address, so the DNS record |
| On the Internet | Cloudflare's edge | Keycloak's sign-in page and the gate; Keycloak locks an account for a few minutes after 10 wrong passwords |

Customers' data belongs on IOH's servers unless IOH approves otherwise, with either way in: use the cloud
environments for trials.
