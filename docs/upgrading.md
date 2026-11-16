# Upgrading the kit

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

From 1.2.x to 1.3.0, unpack and that is all as well: 1.3.0 adds the [environments/](../environments/README.md)
folder (Google Cloud, AWS, a VPS) and the two listen settings above, which default to what the
platform did before. Its images are still 1.2.0's.

From 1.3.0 to 1.4.0, unpack and that is all: 1.4.0 adds the automatic install
(`scripts/autoinstall.sh`, `environments/bootstrap.sh` and `--autoinstall` in the create scripts) and
changes nothing on a platform that is already installed.

From 1.4.0 to 1.5.0, unpack, then `docker compose up -d proxy`: Caddy picks up its new rules, which work
as before while `STAFF_GATE` is off (the default). 1.5.0 adds direct access, the way in without
Cloudflare ([environments/direct-access.md](../environments/direct-access.md)): `settings --way-in`, the
`gate` stage and `dns`, `WAY_IN=direct` in the automatic install, and `--way-in direct` in the create
scripts, which keep a fixed address. The delete scripts now keep that address for a rebuild;
`--release-address` releases it. The images are still 1.2.0's.

From 1.5.0 to 1.6.0, after backing up and unpacking:

```bash
./scripts/install.sh images      # msp-glue 1.3: path tests, capacity forecast, monthly reports
./scripts/install.sh platform    # new Nautobot fields (msp_path_targets, msp_report_language), the path test templates in Zabbix
./scripts/install.sh glue        # msp-glue with the templates and the console's data folder mounted
./scripts/install.sh console     # the console's new pages (skip it if you do not run the console yet)
./scripts/install.sh ai          # optional: a language model on this server for the report text (about 6 GB of memory)
```

What changes: every customer with a site collector gets path test hosts in Zabbix within ten minutes
(four default targets per collector: two internet reference points, Microsoft 365 and Google; add IOH's
edge router at each site in the tenant field `msp_path_targets`, see `edge-collector/README.md`). They
never open tickets and never count as downtime. The first night after the upgrade, msp-glue forecasts
every link that ends a circuit in Nautobot and opens a GLPI ticket ("Managed infrastructure > Capacity
warning") for each one already above 80 % at its busy hour or on course to get there within 45 days;
`capacity.tickets: false` in `glue/mappings.yaml` stops the tickets. The first monthly drafts appear on
the first night of next month, or at once with "Draft now" on the Monthly reports page. `mspctl proxy
add` now prints the right owner for the collector's key file; an existing collector whose log says it
cannot read `zabbix_proxy.psk` needs `sudo chown 1997:1995 zabbix_proxy.psk && sudo chmod 600
zabbix_proxy.psk` and a restart.

The guide's build tab lists the same work as manual steps, for anyone who wants to see or repeat a
single step by hand.

[Back to the README](../README.md)
