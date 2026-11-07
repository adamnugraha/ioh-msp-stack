# Edge collector (customer-site Zabbix proxy)

Use an edge collector when a customer's devices cannot be reached from the central platform:
private addresses that overlap with other customers, firewalls that do not allow inbound
monitoring, or sites behind a slow or unstable link.

* A small VM or appliance at the customer site (2 vCPU, 4 GB RAM, 40 GB disk, any Linux with
  Docker). The disk holds the 72-hour buffer described below.
* It needs only one outbound connection: TCP 10051 to the Zabbix server (or to the platform's
  public address that forwards to it). No inbound rule at the customer side.
* Traffic is encrypted with a pre-shared key unique to the proxy.
* If the link fails, the proxy keeps collecting and stores up to 72 hours of data, then sends
  it when the link returns, so service level reports stay complete.

## Steps

1. On the platform: `docker compose exec msp-glue mspctl proxy add <customer code> <proxy name>`.
   It registers the proxy in the customer's proxy group (`pg-<code>` unless the tenant field
   `msp_proxy_group` names another) and prints the settings below.
2. In Nautobot, set the tenant custom field `msp_proxy_group` to that group, so the customer's
   devices are polled through it. msp-glue moves existing hosts at its next repair run (within
   10 minutes); `docker compose exec msp-glue mspctl device sync <customer code>` moves them at once.
3. On the collector VM, copy this folder, create `.env` and `zabbix_proxy.psk` exactly as
   printed (the key file belongs to user 1997, the proxy's user inside its container, and only it
   may read it), then run `docker compose up -d`.
4. In Zabbix (Administration > Proxies), the proxy shows as Online within a minute.

For two collectors at one site, register both in the same proxy group: Zabbix spreads the hosts
between them and moves hosts automatically if one collector stops.

To change a collector's key, run the same `mspctl proxy add` command again (add `--address` only to
change the address the platform uses to reach it), then replace `zabbix_proxy.psk` at the site and
restart the collector. Between the two steps the collector cannot connect; it keeps collecting and
sends the buffered data once it has the new key.

## Path tests (kit 1.6.0)

Every collector also tests the path from the customer's site: once a minute it pings each target 10
times (delay and loss) or opens a connection to its port, and every 5 minutes it requests the web
targets once (response time and status code). msp-glue keeps one Zabbix host per collector and target,
monitored by that collector, so nothing needs setting up on the VM itself. The MSP Console's Path tests
tab compares three segments to say where a slowdown is:

| Segment | Target | What a problem there means |
|---|---|---|
| `ioh` | IOH's edge router at the site (add it per customer) | IOH's access link, or the customer's own network at the site |
| `internet` | public reference points beyond IOH (1.1.1.1 and 8.8.8.8 by default) | the way to the internet, past IOH's edge |
| `application` | what the customer uses (Microsoft 365 and Google by default) | the application provider, while the path is fine |

Every customer gets the defaults in `glue/mappings.yaml` (section `path_tests`, with the delay, loss and
response time limits). Add a customer's own targets in Nautobot, tenant field "Path test targets"
(`msp_path_targets`), as a JSON list; `collector` limits a target to one collector:

```json
[
  {"name": "IOH edge router Jakarta", "address": "10.20.30.1", "segment": "ioh", "collector": "acme-jkt-01"},
  {"name": "Core banking", "url": "https://core.example.co.id/health", "segment": "application", "http_ms": 1500},
  {"name": "File server", "address": "10.9.9.10", "checks": ["tcp"], "port": 445, "segment": "application"}
]
```

`{"defaults": false, "targets": [...]}` leaves the defaults out, and an entry with the name of a default
replaces it. `mspctl paths list <code>` shows what is tested, from which collectors, and any entry that
could not be used; msp-glue applies changes within ten minutes (`mspctl paths sync <code>` at once).
Path test problems never open GLPI incidents and never count as downtime: the customer's managed devices
already say whether something is down; the path tests say where it is slow.
