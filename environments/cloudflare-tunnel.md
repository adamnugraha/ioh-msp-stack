# Cloudflare Tunnel: the way in outside IOH

On Google Cloud, AWS or a VPS there is no NOC network in front of the platform. Engineers and
customers reach it through a Cloudflare Tunnel instead: a small program (`cloudflared`) on the
platform server connects out to Cloudflare, so the server needs no open web port, and Cloudflare
Access asks engineers who they are before anything reaches the tools.

## 1. The domain

`MSP_DOMAIN` is a domain you added to Cloudflare, and every tool gets a name directly under it:
`console.<domain>`, `sso.<domain>` and so on. Cloudflare's free certificate covers names one level
under the domain only, so `console.example.com` works and `console.msp.example.com` does not (that needs
Cloudflare's paid Advanced Certificate Manager). Each name points at one platform: a second platform,
for a test in another environment, needs a second domain.

## 2. The kit's settings

In the installer's settings step, three values make the tunnel the only way in:

```bash
./scripts/install.sh settings --domain <your domain> --subnet 172.30.0.0/24 \
    --admin-networks "172.30.0.1/32" --web-listen 127.0.0.1 --collectors-listen 127.0.0.1 \
    --tz Asia/Jakarta --email <your e-mail>
```

- `--admin-networks "172.30.0.1/32"`: Caddy sees every visitor arrive from the first address of the
  platform network, where the tunnel's connections come from. With another `--subnet`, use that range's
  first address.
- `--web-listen 127.0.0.1`: ports 80 and 443 answer on this server only, for the tunnel.
- `--collectors-listen 127.0.0.1`: port 10051 too, until a collector at a customer site needs it
  (then `--collectors-listen 0.0.0.0`, the cloud firewall opened to that collector's address only, and
  `docker compose up -d zabbix-server`).

## 3. Cloudflare Access, before any name goes public

Through the tunnel, Caddy sees every visitor arrive from the tunnel's address, which
`--admin-networks` lets through, so Cloudflare Access is what keeps the engineering tools private. Set
it up before the routes of section 4: a route published without it puts that tool on the Internet with
only its own log-in in front.

In Cloudflare Zero Trust, add a self-hosted Access application for every name or path the table puts
behind Access, with a policy that allows only your team's e-mail addresses. Cloudflare's free Zero
Trust plan covers up to 50 users. Customers keep using GLPI and Grafana without it, and Keycloak's
sign-in pages stay reachable for staff (only its `/admin` pages sit behind Access).

| Name | For | Through Cloudflare Access |
|---|---|---|
| `console` | the MSP Console | yes |
| `sso` | staff sign-in (Keycloak) | the path `/admin` only |
| `nautobot`, `zabbix`, `semaphore`, `git`, `glue` | the engineering tools | yes |
| `glpi` | service desk and customer portal | the paths `/apirest.php` and `/api.php` only |
| `grafana` | customer dashboards, when they are on | no |

## 4. The tunnel

After `./scripts/install.sh proxy`, in the Cloudflare dashboard:

1. Networking, Tunnels, **Create Tunnel**. Name it after the environment, for example `msp-gcp`.
2. Under Setup Environment choose Debian, 64-bit. Run the commands shown under Install and Run on
   the platform server, then select Continue once the tunnel shows Healthy.
3. Under Routes, **Add route**, **Published application**, once for each name in the table of section 3:
   - Hostname: the name, and your domain.
   - Service URL: `https://127.0.0.1:443`.
   - Additional application settings, TLS: Origin server name set to the same full name (for example
     `console.<domain>`), and CA pool set to `/opt/ioh-msp-stack/caddy-root.crt`, the certificate the
     proxy stage saved. Cloudflare then checks Caddy's certificate on the hop inside the server.

   Cloudflare creates each name's DNS record itself. It does not replace a record made by hand: delete
   such a record first (DNS, Records).

## 5. Check

From a phone on mobile data, open `https://console.<domain>`: Cloudflare Access asks for your e-mail
address and a code, then Keycloak signs you in, then the console opens. The console's Audit trail page
shows the sign-in with the phone's address next to the tunnel's.

| You see | Cause | Fix |
|---|---|---|
| "This service is only available from the IOH network." | `ADMIN_NETWORKS` lacks the tunnel's address | `./scripts/install.sh settings --admin-networks "172.30.0.1/32"`, then `docker compose up -d proxy` |
| Cloudflare error 502 | The route cannot reach Caddy, or the TLS settings do not match | Service URL `https://127.0.0.1:443`, Origin server name exactly the route's name, CA pool as above |
| Too many redirects | The Service URL uses port 80, which Caddy sends on to HTTPS | Use `https://127.0.0.1:443` |
| The sign-in does not open, or fails after the password | `sso.<domain>` has no route, or a name does not match the domain | Add the `sso` route, with exactly the names above |
| A tool opens without Cloudflare Access asking | The Access application does not cover that name | Add the name to the application, then open the page again in a private browser window |
