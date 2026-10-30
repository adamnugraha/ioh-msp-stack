# A VPS from any provider

One virtual private server (VPS) with a public address, from a local provider or an international
one. There is usually no network firewall in front of it, so the server's own settings do the
protecting: its firewall for SSH, and the kit's listen settings for the ports Docker publishes.
Browsers reach the tools through a Cloudflare Tunnel. Use it for trials, with the sample customers or
IOH's own test equipment: customers' data belongs on IOH's servers unless IOH approves otherwise.

## Steps

1. **Order the server.**
   - Ubuntu Server 24.04 LTS, 64-bit.
   - 4 virtual CPUs, 16 GB of memory and 100 GB of SSD disk run a trial comfortably; 2 CPUs, 8 GB and
     60 GB work, more slowly.
   - A data centre close to the users: Jakarta, or else Singapore.
   - Your SSH public key, if the order form asks for one.
   - If the provider's panel has a firewall: allow inbound TCP 22 from your own address only, and
     nothing else. The tunnel connects out and needs no inbound port.

2. **Connect** with the address and account the provider gives you (often `root`):

   ```bash
   ssh root@<server address>
   ```

   A personal account with sudo is better than root for everything after this step (these lines copy
   root's SSH key to it, when you log in with a key):

   ```bash
   adduser adam && usermod -aG sudo adam
   mkdir -p /home/adam/.ssh && cp ~/.ssh/authorized_keys /home/adam/.ssh/ && chown -R adam: /home/adam/.ssh
   ```

   Then log in as that account: `ssh adam@<server address>`.

3. **Your address.** On the PC you SSH from, open `https://checkip.amazonaws.com`. That address with
   `/32` is the only one the server's firewall lets reach SSH. If it changes often (a home connection),
   find the provider's web console first: it still works when SSH is locked out.

4. **Prepare the server.**

   ```bash
   curl -fL -o /tmp/ioh-msp-stack.zip https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist/ioh-msp-stack-v1.3.0.zip
   sha256sum /tmp/ioh-msp-stack.zip      # must equal the line for this file in dist/SHA256SUMS
   command -v unzip >/dev/null || (sudo apt-get update -qq && sudo apt-get install -y unzip)
   sudo mkdir -p /opt/ioh-msp-stack && sudo chown "$(id -un):" /opt/ioh-msp-stack
   rm -rf /tmp/kit && mkdir /tmp/kit && unzip -q /tmp/ioh-msp-stack.zip -d /tmp/kit
   cp -a /tmp/kit/*/. /opt/ioh-msp-stack/ && chmod +x /opt/ioh-msp-stack/scripts/*.sh
   cd /opt/ioh-msp-stack
   sudo ./scripts/prepare-server.sh --admin-networks "<your address>/32" --pool 172.31.0.0/16 --tz Asia/Jakarta
   ```

   The firewall then allows SSH from your address and from the session you are in. Keep this session
   open, prove SSH works from a second window, then log out and in again so the docker group applies.
   If the provider gives the server a private network too, check that neither 172.31.0.0/16 nor
   172.30.0.0/24 is part of it (`ip route` lists the networks; the preflight check refuses an overlap).

5. **Install.**

   ```bash
   cd /opt/ioh-msp-stack
   ./scripts/install.sh preflight
   ./scripts/install.sh settings --domain <your Cloudflare domain> --subnet 172.30.0.0/24 \
       --admin-networks "172.30.0.1/32" --web-listen 127.0.0.1 --collectors-listen 127.0.0.1 \
       --tz Asia/Jakarta --email <your e-mail>
   tmux new -A -s msp            # a dropped connection does not stop the installer
   ./scripts/install.sh          # 20 to 45 minutes
   ./scripts/install.sh proxy
   ```

   On a VPS the two listen settings matter most: Docker's published ports bypass the server's
   firewall, so `--web-listen 127.0.0.1` and `--collectors-listen 127.0.0.1` are what keep ports 80,
   443 and 10051 off the Internet. The rest is explained in
   [../cloudflare-tunnel.md](../cloudflare-tunnel.md).

6. **The way in.** Set up the Cloudflare Tunnel and Cloudflare Access:
   [../cloudflare-tunnel.md](../cloudflare-tunnel.md), sections 3 to 5.

7. **Staff sign-in, the console and practice data.**

   ```bash
   ./scripts/install.sh sso --admin-email <your e-mail> --name "<your name>"
   ./scripts/install.sh console
   ./scripts/install.sh sso-tools
   ./scripts/install.sh samples      # optional: two made-up customers with simulated devices
   ```

## Day to day

- **Check what is open** from another machine: `nc -vz <server address> 443` should fail, and
  `sudo ss -ltnp | grep -E ':(80|443|10051) '` on the server should show `127.0.0.1` only.
- **E-mail.** Many providers block outgoing port 25. GLPI's notifications and
  `./scripts/install.sh chat email` need a mail service on port 587 or 465.
- **A collector at a customer site** connects to port 10051: allow it in the provider's firewall from
  the collector's address only, then `./scripts/install.sh settings --collectors-listen 0.0.0.0` and
  `docker compose up -d zabbix-server`. Without a provider firewall, port 10051 is then open to everyone;
  every collector still needs its own pre-shared key, but keep such a VPS for trials.
- **Backups.** `sudo ./scripts/backup.sh` works as everywhere; copy `/var/backups/msp` off the server.

When the trial ends, cancel the server with the provider and delete the tunnel in Cloudflare
(Networking, Tunnels).
