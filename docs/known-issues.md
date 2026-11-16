# Known issues found during testing (October and November 2026)

* Zabbix's own templates tag themselves `target` (for example `target: icmp`), and a host tag of the same
  name ends up on the same problem as a second value. The path test hosts name theirs `path_target`.
* Zabbix accepts only version 4 UUIDs in a template file; `zabbix/templates/msp_path_tests.yaml` uses
  fixed ones of that form, so an import updates the templates instead of duplicating them.

* scrapli 2026.10.3 (released 3 October 2026) breaks nornir-scrapli and every Golden Config job.
  `components/nautobot/requirements.txt` pins `scrapli<2026.10`.
* Golden Config installs a "Default Settings" whose scope is every device. A job run for one customer
  then also tries that customer's servers and planned devices and fails (E3018). The bootstrap removes
  it once; customer settings cover active network devices only.
* Golden Config's multi-device job hides errors unless "fail job on task failure" is ticked;
  msp-glue's nightly schedules tick it and run one schedule per customer.
* Nautobot 3.2 reports the deletion of a device that has a primary IP address as an "updated" event.
  msp-glue treats a device that no longer exists as deleted and disables its Zabbix host.
* Zabbix 7.0 does not resolve `{EVENT.UPDATE.NSEVERITY}` in webhook parameters; the GLPI MSP
  webhook uses `{EVENT.NSEVERITY}` instead.
* Semaphore ignores the host limit sent with a task unless the template allows it, and re-enables the
  Ansible Galaxy install when a template allows the task to override it. msp-glue sets
  `allow_override_limit` on and `allow_override_skip_galaxy_install` off on every template.
* GLPI's API only hashes a password sent together with an identical `password2`; msp-glue always
  sends both.
* A GLPI API session does not see an entity created during that session, and GLPI then refuses to add
  groups or rules to it ("You don't have permission to perform this action"). msp-glue refreshes the
  session's active entities right after it creates a customer entity.
* GLPI refuses webhook URLs with a port number and Nautobot refuses host names without a dot,
  so msp-glue listens on port 80 under the alias `msp-glue.platform.internal`.
* Grafana 13.2 cannot delete an organisation through its API, and without automatic organisation
  assignment it creates a personal organisation for every new user. The kit assigns new users to
  "Main Org." and msp-glue moves customer users into their own organisation.
* Docker's default address pools (172.17.0.0/16 and up, 192.168.0.0/16) can overlap networks you
  manage and hide them behind a local route. Create the `msp` network with an explicit subnet and set
  `default-address-pools` in `/etc/docker/daemon.json` to a range no customer uses.
* Docker hands out container addresses again after its own restart, so a lab device without a fixed
  address can end up with another container's old address while Nautobot still points at it. The lab
  devices now have fixed addresses (.201 to .204 of the platform network).
* Zabbix shows the name of the user who acknowledged a problem only to recipients who share a user
  group with that user; otherwise the follow-up says "Inaccessible user". The console acknowledges as
  svc-msp-glue, which `./scripts/install.sh console` adds to the NOC group, and names the engineer in
  the message.
* Keycloak behind a reverse proxy: `KC_HOSTNAME=https://sso.<domain>` with
  `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` gives browsers the public address and lets the console talk
  to `http://keycloak:8080` directly; the issuer stays the public one, so tokens check out either way.
* Versus Incident's message templates know a limited set of functions (`int` is missing, for example);
  the kit's templates use only what the tested 1.4.13 offers. Teams turns each line with a link into
  one clickable block, so the Teams template puts one link per line.
* The Nautobot base image (`networktocode/nautobot:3.2.6-py3.12`) lags Debian's security updates. The
  kit's Nautobot image applies them while it builds (best effort: the preflight warns when
  deb.debian.org cannot be reached). Trivy still reports four HIGH findings in msgpack, urllib3 and
  setuptools: these are copies that pip bundles inside itself, used only while pip runs, and they clear
  when pip updates its bundled copies.
* Versus Incident 1.4 releases add AI and enterprise features quickly. The kit uses only its alert
  path, with the agent, queues and on-call switched off; test a newer `VERSUS_IMAGE` in a lab first.
* **Zabbix keeps counting a problem that was open when its host was disabled** (Zabbix bug
  [ZBX-27917](https://support.zabbix.com/browse/ZBX-27917), confirmed for 7.0, not fixed yet). msp-glue
  disables, never deletes, the host of a device that is removed from Nautobot or no longer active, so a
  device that was down when it was retired keeps its customer's service "down" and lowers the monthly
  availability until the host is deleted. The console no longer shows those problems (as Zabbix's own
  Problems page), but the availability figure is Zabbix's. Until msp-glue handles it: after retiring a
  device that was down, delete its host in Zabbix (Data collection, Hosts) once its history is not
  needed; in the lab the customer's service recovered within a minute.
* The official GLPI image runs PHP in UTC, so GLPI showed and stored ticket times in UTC and read the
  "Business hours WIB" calendar as UTC hours. From 1.2.0 the installer switches on GLPI's time zone
  support and sets the platform's zone (`glpi-api`); GLPI keeps date-times as time stamps, so the times
  GLPI recorded itself (opened, solved, due) keep their real moments. Times someone typed into GLPI by
  hand while it showed UTC were read as UTC hours and now show 7 hours later (WIB); due times already
  set on open bronze tickets stay as they were computed. The console reads GLPI's own clock either way.
* Keycloak's default `role_list` scope sends one SAML `Role` attribute per role, and Zabbix refuses an
  assertion that repeats an attribute ("Found an Attribute element with duplicated Name"). The kit's
  Zabbix client sends every role in one attribute instead.
* Semaphore stops at start-up when `SEMAPHORE_OIDC_PROVIDERS` is set but empty; the kit's default is
  `{}` (no provider). Its manual endpoint settings are named `issuer`, `auth`, `token`, `userinfo`,
  `jwks` and `algorithms`.

[Back to the README](../README.md)
