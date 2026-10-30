# IOH private cloud

The environment the kit was built for: a virtual machine in IOH's private cloud, inside IOH's
network. The main [README](../../README.md) ("Install on a server") and the interactive install guide
(Where the server runs: IOH private cloud) cover it step by step. What sets it apart from the other
environments:

- **The server** comes from the private cloud team, with a fixed address and console access.
- **SSH and the engineering tools** answer only from the NOC and VPN ranges: `--admin-networks` in
  `prepare-server.sh` (the server's firewall) and in the installer's settings (Caddy's access rule).
- **Internet access** usually goes through IOH's web proxy: `prepare-server.sh --proxy <URL>`, plus
  `--proxy-ca <file>` when the proxy inspects TLS. The preflight check lists every address the
  installation needs.
- **Names and certificates** come from IOH: DNS records for each tool under the platform domain, and
  a certificate from IOH's certificate authority (`./scripts/install.sh certs`), so NOC PCs trust the
  tools without installing anything.
- **Customers' data** may live here: it is IOH's own data centre.
- **Listen settings** stay at their default, `0.0.0.0`: the NOC, customers using the GLPI portal and
  collectors at customer sites reach the server directly through IOH's network. A Cloudflare Tunnel is
  optional here (for staff outside the VPN); with one, keep Cloudflare Access in front of the engineers'
  names, as [../cloudflare-tunnel.md](../cloudflare-tunnel.md) describes.
