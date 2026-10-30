# Where the platform runs

The platform is one Ubuntu 24.04 server with Docker, so the kit, its installer and every stage are
the same wherever that server runs. What changes is the ground around it: how the server is created,
how you reach it with SSH, which firewall protects it, and how browsers reach the tools. Each folder
here covers one environment, step by step:

| Environment | Folder | Server | SSH | Firewall | Browsers reach the tools through |
|---|---|---|---|---|---|
| IOH private cloud | [`ioh-private-cloud/`](ioh-private-cloud/README.md) | A virtual machine from the private cloud team | From the NOC and VPN ranges | The server's own (`prepare-server.sh`) and IOH's network | IOH's DNS and network, with IOH certificates |
| Google Cloud | [`gcp/`](gcp/README.md) | `gcp/create.sh` in Cloud Shell | Google's Identity-Aware Proxy (IAP), no open port | Google Cloud's network firewall | A Cloudflare Tunnel |
| Amazon Web Services | [`aws/`](aws/README.md) | `aws/create.sh` in AWS CloudShell | From your own address | The instance's security group | A Cloudflare Tunnel |
| A VPS from any provider | [`vps/`](vps/README.md) | Ordered from the provider | From your own address | The server's own (`prepare-server.sh`) and the provider's, if it has one | A Cloudflare Tunnel |

The interactive install guide "IOH MSP Platform Install" has the same choice at the top ("Where the
server runs"): every command then matches the environment and your own values.

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
  clouds, the cloud's firewall in front of the server does the job. Everywhere outside IOH, the settings
  `--web-listen 127.0.0.1` and `--collectors-listen 127.0.0.1` keep those ports on the server itself,
  because a Cloudflare Tunnel on the same server is the way in.
- **The way in.** Outside IOH there is no NOC network, so engineers come through a Cloudflare Tunnel
  with Cloudflare Access in front: [cloudflare-tunnel.md](cloudflare-tunnel.md). Caddy then sees every
  visitor arrive from the tunnel's own address, the first address of the platform network (172.30.0.1
  by default), and that one address is the only entry in `ADMIN_NETWORKS`.
- **E-mail.** Google Cloud and AWS block outgoing port 25. GLPI's notifications and the e-mail chat
  channel need a mail service on port 587 (STARTTLS) or 465 (TLS); the kit's default is 587.
- **Data.** Customers' data belongs on IOH's own servers unless IOH approves otherwise (personal data
  protection law, banking regulation). Use the cloud environments for trials, with the sample customers
  (`./scripts/install.sh samples`) or IOH's own test equipment.
- **Cost.** A cloud server costs money while it runs. Stop it between tests (its disk still costs a
  little), set a budget alert, and remove everything with the environment's `delete.sh` when done.
