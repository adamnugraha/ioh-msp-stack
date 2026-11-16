# Extending the platform

Most of what the platform does is decided in one file, [`glue/mappings.yaml`](../glue/mappings.yaml), and in two
Git repositories in Forgejo that every customer shares: `msp/playbooks` and `msp/golden-config`. Code changes are
needed only for a new kind of action or a new tool. Each section below says what to change, how to apply it, and
how to check it.

After any change to `glue/mappings.yaml`:

```bash
docker compose restart msp-glue msp-console
docker compose exec msp-glue mspctl tenant sync --all   # apply to existing customers now (the repair run does it within 10 minutes)
docker compose exec msp-glue mspctl device sync         # and to their devices
```

Try every change on the lab first (`./lab/run-lab.sh`, then `python3 lab/check.py`).

## A new playbook

1. Write the playbook in the `msp/playbooks` repository in Forgejo (`https://git.<domain>`; the automation team
   can write there). The folder `semaphore/playbooks/` in this kit is only the starter content, uploaded once by
   `mspctl bootstrap platform`; add the file there too if you want it in a rebuilt platform.
2. Target the hosts the run is limited to. A playbook that changes devices names them with
   `hosts: "{{ device }}"`, as [`bounce-interface.yml`](../semaphore/playbooks/playbooks/bounce-interface.yml) does,
   so it can never run on the whole inventory by mistake.
3. Add it to `semaphore.templates` in `glue/mappings.yaml`:

   ```yaml
   - name: "Restart DHCP relay"
     playbook: "playbooks/restart-dhcp-relay.yml"
     description: "Restarts the DHCP relay on one router. Needs an approved change."
   ```

4. If it changes anything, add its name to `console.change_only_templates`: the console then starts it only for an
   approved GLPI change of the same customer.
5. Restart msp-glue as above. Every customer's Semaphore project gets the template at the next repair run.

Credentials never go in the repository: Semaphore logs in with the customer's `device-login` key, which msp-glue
fills from `secrets/tenants/<code>/`.

## A playbook that runs when a change is approved

1. Map a GLPI change category to the template in `automation.change_templates`:

   ```yaml
   "Managed infrastructure > Standard change > Restart DHCP relay": "Restart DHCP relay"
   ```

2. Run `docker compose exec msp-glue mspctl bootstrap platform` once: it creates the category in GLPI.
3. An engineer raises a change in that category with an `automation:` block in the description:

   ```text
   automation:
   device: jkt01-rtr01
   interface: GigabitEthernet0/1
   ```

4. When the change is set to Accepted, msp-glue starts the template on that device and writes the task number, and
   later the result with the last 40 lines of the log, to the change. Ansible's own variables (`ansible_*`) are
   never taken from a description. The console's "New > Change" dialog writes the block for you.

## Monitoring for a new kind of device

1. In Nautobot, create the platform with its network driver (Devices > Platforms), or add it to `PLATFORMS` in
   [`glue/msp_glue/bootstrap.py`](../glue/msp_glue/bootstrap.py) so every new platform server has it.
2. In `zabbix.platforms`, map the network driver to the Zabbix templates and the interface type:

   ```yaml
   ruckus_fastiron: {interface: snmp, templates: ["Network Generic Device by SNMP"]}
   ```

   Template names must match Zabbix exactly (Data collection > Templates).
3. Restart msp-glue and run `mspctl device sync`. Devices on that platform get the templates.

## Configuration backups and compliance for a new platform

1. Add the platform's name to `golden_config.platforms`.
2. In the `msp/golden-config` repository, add `templates/<network_driver>.j2` (the intended configuration) and,
   for compliance, rules under `golden_config/compliance_rules/`. The kit's Cisco files are examples.
3. In Nautobot, open Extensibility > Git Repositories > golden-config and press Sync.

Platform standards belong in the config context `msp-standards`; a customer's own values belong in a config context
scoped to its tenant, never in a template. See [golden-config/README.md](../golden-config/README.md).

## Service tiers, targets and ticket rules

| What | Where in `glue/mappings.yaml` |
|---|---|
| Availability target per tier (99.9, 99.5, 99.0) | `zabbix.sla.slo` |
| Which severity counts as downtime | `zabbix.sla.severity` |
| Time to own and time to resolve per tier, and their calendar | `glpi.service_tiers`, `glpi.calendars` |
| Ticket categories, the NOC group | `glpi.categories`, `glpi.noc_group` |
| Which severity reaches the NOC chat, and the wait before it | `chat.min_severity`, `chat.delay` |
| Path test targets for every customer, and their limits | `path_tests.defaults`, `path_tests.limits` |
| Link capacity warnings and their tickets | `capacity` |
| When monthly report drafts are made, and their default language | `reports` |

A customer's own tier, path test targets, report language and proxy group are fields on its Nautobot tenant.

## A site collector for a customer

A customer whose devices the platform cannot reach gets a Zabbix proxy at its site: one VM, one outbound
connection. The steps are in [edge-collector/README.md](../edge-collector/README.md), starting with
`mspctl proxy add <code> <name>`.

## A new page or action in the MSP Console

The console's pages (React) and its service (FastAPI) agree on one contract.

1. Describe what the page reads or the action sends in
   [`glue/console-ui/src/lib/api/types.ts`](../glue/console-ui/src/lib/api/types.ts).
2. Answer it in [`glue/msp_glue/console/api.py`](../glue/msp_glue/console/api.py): reads go through `views.py` or
   `detail.py`; a change goes through a function in `actions.py` that checks the person's role and writes the
   audit entry before it calls a tool.
3. Add the page in `glue/console-ui/src/pages/` and its route in `src/routes.tsx`. A new top-level address also
   goes in `PAGES` in [`console/app.py`](../glue/msp_glue/console/app.py), so a bookmark to it works.
4. Add sample data in `src/lib/api/demo/`, so the preview shows it, then run the tests: `glue/tests/contract.py`
   checks every answer of the service against `types.ts`.

Work on the pages with `npm run dev:demo` (sample data) or `npm run dev` (a lab console); see
[glue/console-ui/README.md](../glue/console-ui/README.md).

## A new tool

1. Add `components/<tool>/compose.yaml` with a profile, so it starts only when switched on, and include it in
   [`compose.yaml`](../compose.yaml). Grafana's and Versus Incident's files are short examples.
2. Give it an address in [`components/proxy/Caddyfile`](../components/proxy/Caddyfile): engineers' tools take
   `import engineers_only` and the sign-in gate, as Nautobot's block does.
3. Put its settings and secrets in `.env.example`, with `__RANDOM__` for generated secrets.
4. If it holds something per customer, add a client in `glue/msp_glue/clients/` and one `ensure_<tool>()` step in
   `tenants.ensure_tenant()`. Each step must be safe to run again, because the repair run calls it every
   10 minutes.
5. Add its data volume to [`scripts/backup.sh`](../scripts/backup.sh) and an install stage to `scripts/install.sh`.

## The figures in the README

Each figure is an HTML page in [`docs/assets/src/`](assets/src/), styled by `style.css` in the same folder. Change
the text, open the page in Chrome at 1200 pixels wide and save a screenshot at 1.5x as the PNG beside it. The
console screenshots come from the preview with sample data (`npm run build:demo` in `glue/console-ui`).

[Back to the README](../README.md)
