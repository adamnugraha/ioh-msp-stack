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
   printed, then run `docker compose up -d`.
4. In Zabbix (Administration > Proxies), the proxy shows as Online within a minute.

For two collectors at one site, register both in the same proxy group: Zabbix spreads the hosts
between them and moves hosts automatically if one collector stops.

To change a collector's key, run the same `mspctl proxy add` command again (add `--address` only to
change the address the platform uses to reach it), then replace `zabbix_proxy.psk` at the site and
restart the collector. Between the two steps the collector cannot connect; it keeps collecting and
sends the buffered data once it has the new key.
