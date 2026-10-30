# Amazon Web Services

One EC2 instance in Jakarta (`ap-southeast-3`), in the Region's default network, behind a security
group that lets in SSH from your own address and nothing else. Browsers reach the tools through a
Cloudflare Tunnel. Use it for trials, with the sample customers or IOH's own test equipment:
customers' data belongs on IOH's servers unless IOH approves otherwise.

**Cost** (list prices in October 2026; check the current ones in the AWS Pricing Calculator):
m7i-flex.xlarge (4 vCPU, 16 GB) about US$0.24 an hour, so about US$175 a month if it never stops,
plus about US$10 a month for the 100 GB gp3 disk. A stopped instance costs only its disk. New AWS
accounts start on the free plan with up to US$200 of credits for six months; on it, m7i-flex.large
(2 vCPU, 8 GB, enough for a trial) is one of the sizes allowed. A larger size needs the paid plan,
and the credits left still apply there.

## Steps

1. **Account and the Jakarta Region.** Jakarta is an opt-in Region: in the AWS console, open your
   account menu, Account, AWS Regions, Asia Pacific (Jakarta), and enable it. It takes a few minutes,
   sometimes longer; wait until it shows Enabled.

2. **CloudShell and the kit.** Open AWS CloudShell (the `>_` button at the top of the console),
   where the AWS CLI is ready and signed in. Get the kit there, only to run its scripts:

   ```bash
   curl -fL -o kit.zip https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist/ioh-msp-stack-v1.3.0.zip
   unzip -q kit.zip && cd ioh-msp-stack
   ```

3. **Your address.** On the PC you will SSH from (or on the server that runs your SSH client, such
   as Termix), open `https://checkip.amazonaws.com`. That address with `/32` is the only one the
   security group lets in. CloudShell's own address is not it.

4. **Create the server** (2 to 3 minutes):

   ```bash
   ./environments/aws/create.sh --ssh-from <your address>/32
   ```

   It checks the Region, finds Canonical's latest Ubuntu 24.04 image, makes the key pair
   `msp-test-01` (its private key is saved as `msp-test-01.pem` in CloudShell), the security group
   `msp-test-01-sg`, and the instance `msp-test-01`: m7i-flex.xlarge, a 100 GB gp3 disk. On the free
   plan add `--type m7i-flex.large`. With `--public-key ~/.ssh/id_ed25519.pub` (uploaded to CloudShell
   first: Actions, Upload file) it uses your own key instead. `--dry-run` shows the commands without
   running them; `--help` lists every option. Running it again is safe.

   Download `msp-test-01.pem` to your PC now (CloudShell: Actions, Download file): AWS never shows the
   private key again. The script ends with the address to connect to, and the Docker address pool to use
   on the server (172.29.0.0/16, because AWS's default network is 172.31.0.0/16, the kit's usual pool).

5. **Connect** from your PC (Windows: PowerShell has `ssh`; PuTTY and Termix import the `.pem` file):

   ```bash
   ssh -i msp-test-01.pem ubuntu@<its address>
   ```

6. **Prepare the server.** On the server, as `ubuntu`:

   ```bash
   curl -fL -o /tmp/ioh-msp-stack.zip https://raw.githubusercontent.com/adamnugraha/ioh-msp-stack/main/dist/ioh-msp-stack-v1.3.0.zip
   sha256sum /tmp/ioh-msp-stack.zip      # must equal the line for this file in dist/SHA256SUMS
   command -v unzip >/dev/null || (sudo apt-get update -qq && sudo apt-get install -y unzip)
   sudo mkdir -p /opt/ioh-msp-stack && sudo chown "$(id -un):" /opt/ioh-msp-stack
   rm -rf /tmp/kit && mkdir /tmp/kit && unzip -q /tmp/ioh-msp-stack.zip -d /tmp/kit
   cp -a /tmp/kit/*/. /opt/ioh-msp-stack/ && chmod +x /opt/ioh-msp-stack/scripts/*.sh
   cd /opt/ioh-msp-stack
   sudo ./scripts/prepare-server.sh --no-firewall --pool 172.29.0.0/16 --tz Asia/Jakarta
   ```

   `--no-firewall` leaves the server's own firewall off: the security group in front of it lets in
   nothing but SSH from your address. Then type `exit` and connect again (step 5), so your account's
   docker group applies.

7. **Install.**

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

8. **The way in.** Set up the Cloudflare Tunnel and Cloudflare Access:
   [../cloudflare-tunnel.md](../cloudflare-tunnel.md), sections 3 to 5.

9. **Staff sign-in, the console and practice data.**

   ```bash
   ./scripts/install.sh sso --admin-email <your e-mail> --name "<your name>"
   ./scripts/install.sh console
   ./scripts/install.sh sso-tools
   ./scripts/install.sh samples      # optional: two made-up customers with simulated devices
   ```

   Then open `https://console.<your domain>` and sign in.

## Day to day

```bash
aws ec2 stop-instances --instance-ids <instance ID> --region ap-southeast-3
aws ec2 start-instances --instance-ids <instance ID> --region ap-southeast-3
aws ec2 describe-instances --instance-ids <instance ID> --region ap-southeast-3 \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text     # the address after a start
```

Every container starts again with the instance, and the tunnel reconnects by itself. The public
address changes at every start; the tunnel does not mind, SSH needs the new one.

- **Your own address changed** (a new office, a home connection): run `create.sh` again with the new
  `--ssh-from`, and remove the old address from the security group (EC2, Security groups,
  `msp-test-01-sg`, Inbound rules).
- **E-mail.** AWS blocks outgoing port 25 from EC2. GLPI's notifications and
  `./scripts/install.sh chat email` need a mail service on port 587 or 465 (Amazon SES works).
- **A collector at a customer site** connects to port 10051. Run `create.sh` again with
  `--collectors-from "<collector address>/32"`, then on the server
  `./scripts/install.sh settings --collectors-listen 0.0.0.0` and `docker compose up -d zabbix-server`.
  The collector's `ZBX_SERVER_HOST` is the instance's public address; give the instance an Elastic IP
  so it stays the same.
- **Backups.** `sudo ./scripts/backup.sh` works as everywhere; copy `/var/backups/msp` off the server
  (Amazon S3, for example) if the trial data matters.

## Remove everything

In CloudShell, from the kit folder:

```bash
./environments/aws/delete.sh
```

It terminates the instance with its disk and deletes the security group and the key pair. Delete the
tunnel in Cloudflare too (Networking, Tunnels).
