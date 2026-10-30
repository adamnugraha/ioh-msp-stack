# Google Cloud

One Compute Engine virtual machine in Jakarta (`asia-southeast2`), in a network of its own where
only SSH through Google's Identity-Aware Proxy (IAP) gets in. Browsers reach the tools through a
Cloudflare Tunnel. Use it for trials, with the sample customers or IOH's own test equipment:
customers' data belongs on IOH's servers unless IOH approves otherwise.

**Cost** (Google's list prices in October 2026; check the current ones in the pricing calculator):
e2-standard-4 (4 vCPU, 16 GB) about US$0.18 an hour, so about US$130 a month if it never stops,
plus about US$13 a month for the 100 GB disk and a few dollars for its external address. A stopped
machine costs only its disk. New Google Cloud accounts get a Free Trial of US$300 for 90 days, with
no automatic charge when it ends.

## Steps

1. **Project.** In the Google Cloud console (console.cloud.google.com), create a project, for
   example `ioh-msp-test`, and link a billing account to it (a Free Trial account works). Note its
   project ID, which can differ from its name.

2. **Cloud Shell and the kit.** Open Cloud Shell (the `>_` button at the top of the console), where
   `gcloud` is ready and signed in. Get the kit there, only to run its scripts:

   ```bash
   curl -fL -o kit.zip https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist/ioh-msp-stack-v1.3.0.zip
   unzip -q kit.zip && cd ioh-msp-stack
   ```

3. **Create the server** (2 to 3 minutes):

   ```bash
   ./environments/gcp/create.sh --project <project ID>
   ```

   It switches on Compute Engine, creates the network `msp-net` (subnet 10.200.0.0/24 in
   Jakarta) with one firewall rule (SSH from IAP's range 35.235.240.0/20), and the virtual machine
   `msp-test-01`: e2-standard-4, Ubuntu 24.04, a 100 GB balanced disk. `--dry-run` shows the
   commands without running them, and `--help` lists the options: another size, plain SSH from your
   own client (Termix, PuTTY) with `--ssh-from` and `--public-key`, or port 10051 for collectors.
   Running it again is safe.

4. **Connect.** The first time, gcloud makes an SSH key; press Enter at its questions.

   ```bash
   gcloud compute ssh msp-test-01 --project <project ID> --zone asia-southeast2-a --tunnel-through-iap
   ```

5. **Prepare the server.** On the server, as your own account:

   ```bash
   curl -fL -o /tmp/ioh-msp-stack.zip https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist/ioh-msp-stack-v1.3.0.zip
   sha256sum /tmp/ioh-msp-stack.zip      # must equal the line for this file in dist/SHA256SUMS
   command -v unzip >/dev/null || (sudo apt-get update -qq && sudo apt-get install -y unzip)
   sudo mkdir -p /opt/ioh-msp-stack && sudo chown "$(id -un):" /opt/ioh-msp-stack
   rm -rf /tmp/kit && mkdir /tmp/kit && unzip -q /tmp/ioh-msp-stack.zip -d /tmp/kit
   cp -a /tmp/kit/*/. /opt/ioh-msp-stack/ && chmod +x /opt/ioh-msp-stack/scripts/*.sh
   cd /opt/ioh-msp-stack
   sudo ./scripts/prepare-server.sh --no-firewall --pool 172.31.0.0/16 --tz Asia/Jakarta
   ```

   `--no-firewall` leaves the server's own firewall off: the network's firewall rule in front of it
   lets in nothing but SSH through IAP. Then type `exit` and connect again (step 4), so your account's
   docker group applies.

6. **Install.**

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

   The domain and the three values after `--subnet` are explained in
   [../cloudflare-tunnel.md](../cloudflare-tunnel.md).

7. **The way in.** Set up the Cloudflare Tunnel and Cloudflare Access:
   [../cloudflare-tunnel.md](../cloudflare-tunnel.md), sections 3 to 5.

8. **Staff sign-in, the console and practice data.**

   ```bash
   ./scripts/install.sh sso --admin-email <your e-mail> --name "<your name>"
   ./scripts/install.sh console
   ./scripts/install.sh sso-tools
   ./scripts/install.sh samples      # optional: two made-up customers with simulated devices
   ```

   Then open `https://console.<your domain>` and sign in.

## Day to day

```bash
gcloud compute instances stop msp-test-01 --zone asia-southeast2-a --project <project ID>
gcloud compute instances start msp-test-01 --zone asia-southeast2-a --project <project ID>
```

Every container starts again with the machine, and the tunnel reconnects by itself.

- **E-mail.** Google Cloud blocks outgoing port 25. GLPI's notifications and `./scripts/install.sh chat
  email` need a mail service on port 587 or 465.
- **A collector at a customer site** (for example on your own VPS) connects to port 10051. Run
  `create.sh` again with `--collectors-from "<collector address>/32"`, then on the server
  `./scripts/install.sh settings --collectors-listen 0.0.0.0` and `docker compose up -d zabbix-server`.
  The collector's `ZBX_SERVER_HOST` is the machine's external address. That address changes when the
  machine stops and starts, so keep it:
  `gcloud compute addresses create msp-test-01-ip --addresses <its address> --region asia-southeast2 --project <project ID>`.
- **Backups.** `sudo ./scripts/backup.sh` works as everywhere; copy `/var/backups/msp` off the server
  (Cloud Storage, for example) if the trial data matters.

## Remove everything

In Cloud Shell, from the kit folder:

```bash
./environments/gcp/delete.sh --project <project ID>
```

It deletes the virtual machine with its disk, the firewall rules and the network. Delete the tunnel
in Cloudflare too (Networking, Tunnels), and the project itself if nothing else uses it.
