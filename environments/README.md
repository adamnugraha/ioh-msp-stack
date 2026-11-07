# Where the platform runs

The platform is one Ubuntu 24.04 server with Docker, so the kit, its installer and every stage are
the same wherever that server runs. What changes is the ground around it: how the server is created,
how you reach it with SSH, which firewall protects it, and how browsers reach the tools. Each folder
here covers one environment, step by step:

| Environment | Folder | Server | SSH | Firewall | Browsers reach the tools through |
|---|---|---|---|---|---|
| IOH private cloud | [`ioh-private-cloud/`](ioh-private-cloud/README.md) | A virtual machine from the private cloud team | From the NOC and VPN ranges | The server's own (`prepare-server.sh`) and IOH's network | IOH's DNS and network, with IOH certificates |
| Google Cloud | [`gcp/`](gcp/README.md) | `gcp/create.sh` in Cloud Shell | Google's Identity-Aware Proxy (IAP), no open port | Google Cloud's network firewall | A Cloudflare Tunnel, or direct access |
| Amazon Web Services | [`aws/`](aws/README.md) | `aws/create.sh` in AWS CloudShell | From your own address | The instance's security group | A Cloudflare Tunnel, or direct access |
| A VPS from any provider | [`vps/`](vps/README.md) | Ordered from the provider | From your own address | The server's own (`prepare-server.sh`) and the provider's, if it has one | A Cloudflare Tunnel, or direct access |

The interactive install guide "IOH MSP Platform Install" has the same choice at the top ("Where the
server runs"): every command then matches the environment and your own values.

## Two ways in outside IOH

| | Cloudflare Tunnel | Direct access |
|---|---|---|
| How browsers come in | through Cloudflare; the server opens no web port | straight to the server's fixed address, ports 80 and 443 |
| Who asks engineers who they are | Cloudflare Access | the kit's sign-in gate, with the staff account in Keycloak |
| Certificates | Cloudflare's | Let's Encrypt's |
| Prepared once | a domain on Cloudflare, Access, the tunnel and its routes | one DNS record, `*.<domain>` |
| The guide | [cloudflare-tunnel.md](cloudflare-tunnel.md) | [direct-access.md](direct-access.md) |

Both keep the engineering tools private and leave GLPI's portal and Grafana open to customers. The
tunnel shows nothing of the server on the Internet; direct access needs no Cloudflare account and no
set-up there. `WAY_IN` in the settings file (automatic install) or `./scripts/install.sh settings
--way-in` (by hand) picks one.

## Automatic install

On Google Cloud, AWS and a VPS the whole platform can install itself, with no questions on the way.
You fill in one settings file ([autoinstall.conf.example](autoinstall.conf.example): the domain, your
staff e-mail and name, and the way in: the tunnel's token, or `WAY_IN=direct`), then:

| Environment | One command | Where |
|---|---|---|
| Google Cloud | `./environments/gcp/create.sh --project <project ID> --autoinstall my.conf` | Cloud Shell |
| AWS | `./environments/aws/create.sh --ssh-from "<your address>/32" --autoinstall my.conf` | AWS CloudShell |
| A VPS | `./environments/bootstrap.sh my.conf ENVIRONMENT=vps SSH_FROM="<your address>/32" > msp-autoinstall.sh`, then that script as the provider's user data, or run on the server as root | Any Linux or macOS shell |

The server then runs `scripts/autoinstall.sh` as a service: Ubuntu's updates, Docker, the account
`msp` that owns the kit, every installer stage, staff sign-in, HTTPS, the way in (the Cloudflare Tunnel,
or the sign-in gate and Let's Encrypt), the MSP Console, sign-in to the tools, the sample customers and
the nightly backup. With direct access it waits for the DNS record before HTTPS, so create it as soon as
the create script shows the address. It takes 40 to 75 minutes; `sudo /opt/ioh-msp-stack/scripts/autoinstall.sh status` on the server shows how far it is,
the log-in banner shows the same line, and once finished the same command shows the first sign-in.
A step that fails stops the install with its reason and fix; `sudo /opt/ioh-msp-stack/scripts/autoinstall.sh start`
after the fix continues with that step, and so does a restart of the server.

**A rebuild** is the environment's delete script, then the same create command with the same file.
With a tunnel, the Cloudflare side (the tunnel, its routes and Cloudflare Access) stays as it is, so
the new server takes over the same names by itself. With direct access, the delete scripts keep the
fixed address, so the DNS record stays right (Let's Encrypt allows about five rebuilds a week with one
domain). Nothing from the old server is carried over: a rebuilt platform starts empty, with the sample
customers if the file says so.

Prepared once per domain, by hand, before the first build: for a tunnel,
[cloudflare-tunnel.md](cloudflare-tunnel.md), sections 3 and 4 (Access, then the tunnel and its
routes), with the tunnel's token copied into the settings file instead of running Cloudflare's install
command; for direct access, nothing before the build, then the DNS record
([direct-access.md](direct-access.md)).

## The same everywhere

- Ubuntu Server 24.04 LTS. For a trial, 4 virtual CPUs, 16 GB of memory and 100 GB of disk run
  comfortably; 2 CPUs and 8 GB work, more slowly (the lab ran on that). The pilot size is 8 CPUs,
  32 GB and 500 GB.
- The kit download, `prepare-server.sh`, every stage of `install.sh`, the sample customers and every
  day-to-day command in the main README.
- Docker's address pool and the platform network (`--pool`, `--subnet`) must not overlap any network
  the server reaches. `prepare-server.sh` and the preflight check refuse a range that does.

## What differs, and why

- **Firewall.** Docker's published ports bypass the server's own firewall (ufw): ports 80 and 443
  (Caddy) and 10051 (Zabbix, for collectors at customer sites) stay open whatever ufw says. In the
  clouds, the cloud's firewall in front of the server does the job. With a tunnel, the settings
  `--web-listen 127.0.0.1` and `--collectors-listen 127.0.0.1` keep those ports on the server itself;
  with direct access, ports 80 and 443 stay open on purpose and port 10051 stays on the server.
- **The way in.** Outside IOH there is no NOC network. With a Cloudflare Tunnel, Cloudflare Access asks
  engineers who they are ([cloudflare-tunnel.md](cloudflare-tunnel.md)); Caddy then sees every visitor
  arrive from the tunnel's own address, the first address of the platform network (172.30.0.1 by
  default), and that one address is the only entry in `ADMIN_NETWORKS`. With direct access, the kit's
  sign-in gate asks instead ([direct-access.md](direct-access.md)) and `ADMIN_NETWORKS` lets every
  address through, unless you list your own.
- **E-mail.** Google Cloud and AWS block outgoing port 25. GLPI's notifications and the e-mail chat
  channel need a mail service on port 587 (STARTTLS) or 465 (TLS); the kit's default is 587.
- **Data.** Customers' data belongs on IOH's own servers unless IOH approves otherwise (personal data
  protection law, banking regulation). Use the cloud environments for trials, with the sample customers
  (`./scripts/install.sh samples`) or IOH's own test equipment.
- **Cost.** A cloud server costs money while it runs. Stop it between tests (its disk still costs a
  little), set a budget alert, and remove everything with the environment's `delete.sh` when done.
