#!/usr/bin/env python3
"""Lab only: create two sample customers and their devices in Nautobot.

Nautobot then sends webhooks to msp-glue, which provisions everything else.
Run from the kit folder after "mspctl bootstrap platform":  python3 lab/seed.py
"""

import json
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
env = dict(line.strip().split("=", 1) for line in open(ROOT / ".env") if "=" in line and not line.startswith("#"))
API = "http://localhost:8081/api/"
S = requests.Session()
S.headers.update({"Authorization": f"Token {env['NAUTOBOT_SUPERUSER_API_TOKEN']}", "Accept": "application/json",
                  "Content-Type": "application/json"})


def get(path, **params):
    r = S.get(API + path, params=params)
    r.raise_for_status()
    return r.json()["results"]


def ensure(path, lookup, payload):
    found = get(path, **lookup)
    if found:
        return found[0]
    r = S.post(API + path, data=json.dumps(payload))
    if r.status_code >= 400:
        raise SystemExit(f"POST {path} failed: {r.status_code} {r.text}")
    return r.json()


def ip_of(container):
    return subprocess.check_output(
        ["docker", "inspect", "-f", "{{.NetworkSettings.Networks.msp.IPAddress}}", container], text=True).strip()


# The platform network's subnet, for example 172.30.0.0/24 (see lab/run-lab.sh).
SUBNET = subprocess.check_output(
    ["docker", "network", "inspect", "msp", "-f", "{{(index .IPAM.Config 0).Subnet}}"], text=True).strip()
MASK = SUBNET.split("/")[1]


# ---------------------------------------------------------------- secrets (files)
secrets = {
    "acme": {"snmp_community": "acme-ro-2026", "device_username": "netadmin", "device_password": "Lab-Device-Pass1"},
    "bankx": {"snmpv3_user": "ioh-noc", "snmpv3_authpass": "BankxAuth-2026!", "snmpv3_privpass": "BankxPriv-2026!",
              "device_username": "netadmin", "device_password": "Lab-Device-Pass1"},
}
for code, values in secrets.items():
    folder = ROOT / "secrets" / "tenants" / code
    folder.mkdir(parents=True, exist_ok=True)
    for name, value in values.items():
        (folder / name).write_text(value)
        (folder / name).chmod(0o644)
# The acme device-login (Semaphore key and Golden Config secrets) also opens the lab Linux server.

# ----------------------------------------------------------------- reference data
status = {s["name"]: s["id"] for s in get("extras/statuses/", limit=100)}
loc_types = {t["name"]: t["id"] for t in get("dcim/location-types/")}
roles = {r["name"]: r["id"] for r in get("extras/roles/", limit=100)}
platforms = {p["name"]: p["id"] for p in get("dcim/platforms/", limit=100)}
makers = {m["name"]: m["id"] for m in get("dcim/manufacturers/", limit=100)}
group = get("tenancy/tenant-groups/", name="Managed Services Customers")[0]
namespace = get("ipam/namespaces/", name="Global")[0]

# Platform standard used by the Golden Config templates (all customers).
ensure("extras/config-contexts/", {"name": "msp-standards"}, {
    "name": "msp-standards", "weight": 1000, "is_active": True,
    "description": "Platform-wide configuration standards for managed devices",
    "data": {"msp_standards": {"ntp_servers": ["10.255.0.1", "10.255.0.2"], "logging_hosts": ["10.255.0.10"],
                               "snmp_location": "IOH managed", "snmp_contact": "noc@indosatooredoo.com"}},
})

jakarta = ensure("dcim/locations/", {"name": "Jakarta"},
                 {"name": "Jakarta", "location_type": loc_types["Region"], "status": status["Active"]})
westjava = ensure("dcim/locations/", {"name": "West Java"},
                  {"name": "West Java", "location_type": loc_types["Region"], "status": status["Active"]})
c8200 = ensure("dcim/device-types/", {"model": "C8200-1N-4T"}, {"model": "C8200-1N-4T", "manufacturer": makers["Cisco"]})
c9200 = ensure("dcim/device-types/", {"model": "C9200L-24T-4G"}, {"model": "C9200L-24T-4G", "manufacturer": makers["Cisco"]})
linuxvm = ensure("dcim/device-types/", {"model": "Linux VM"}, {"model": "Linux VM", "manufacturer": makers["Generic"]})
ensure("ipam/prefixes/", {"prefix": SUBNET}, {"prefix": SUBNET, "namespace": namespace["id"],
                                              "status": status["Active"], "type": "network",
                                              "description": "Lab platform network"})

# ------------------------------------------------------------------------ tenants
tenants = {
    "acme": {"name": "PT ACME Retail Indonesia", "custom_fields": {
        "msp_code": "acme", "msp_service_tier": "silver", "msp_customer_id": "IOH-100234",
        "msp_snmp_version": "2", "msp_contact_email": "it@acme.example"}},
    "bankx": {"name": "PT Bank Contoh Tbk", "custom_fields": {
        "msp_code": "bankx", "msp_service_tier": "gold", "msp_customer_id": "IOH-100871", "msp_regulated": True,
        "msp_snmp_version": "3", "msp_proxy_group": "pg-bankx", "msp_contact_email": "noc@bankcontoh.example"}},
}
tenant_ids = {}
for code, spec in tenants.items():
    t = ensure("tenancy/tenants/", {"name": spec["name"]},
               {"name": spec["name"], "tenant_group": group["id"], "custom_fields": spec["custom_fields"]})
    tenant_ids[code] = t["id"]
    print(f"tenant {code}: {t['id']}")

sites = {
    "acme-hq": ensure("dcim/locations/", {"name": "ACME HQ Jakarta"}, {
        "name": "ACME HQ Jakarta", "location_type": loc_types["Site"], "parent": jakarta["id"],
        "status": status["Active"], "tenant": tenant_ids["acme"]}),
    "acme-bdg": ensure("dcim/locations/", {"name": "ACME Store Bandung"}, {
        "name": "ACME Store Bandung", "location_type": loc_types["Site"], "parent": westjava["id"],
        "status": status["Active"], "tenant": tenant_ids["acme"]}),
    "bankx-dc": ensure("dcim/locations/", {"name": "Bank Contoh DC Cikarang"}, {
        "name": "Bank Contoh DC Cikarang", "location_type": loc_types["Site"], "parent": westjava["id"],
        "status": status["Active"], "tenant": tenant_ids["bankx"]}),
}

# ------------------------------------------------------------------------ devices
devices = [
    # name, tenant, site, role, platform, type, status, container, serial
    ("jkt01-rtr01", "acme", "acme-hq", "router", "Cisco IOS-XE", c8200, "Active", "msp-lab-devices-lab-acme-rtr01-1", "FGL2601A1BC"),
    ("jkt01-srv01", "acme", "acme-hq", "server", "Linux", linuxvm, "Active", "msp-lab-devices-lab-acme-srv01-1", "VM-0001"),
    ("bdg01-sw01", "acme", "acme-bdg", "switch", "Cisco IOS", c9200, "Planned", None, "JAE2602X0YZ"),
    ("ckr01-rtr01", "bankx", "bankx-dc", "router", "Cisco IOS-XE", c8200, "Active", "msp-lab-devices-lab-bankx-rtr01-1", "FGL2601B2CD"),
]
for name, code, site, role, platform, dtype, st, container, serial in devices:
    dev = ensure("dcim/devices/", {"name": name, "tenant": tenant_ids[code]}, {
        "name": name, "tenant": tenant_ids[code], "location": sites[site]["id"], "role": roles[role],
        "platform": platforms[platform], "device_type": dtype["id"], "status": status[st], "serial": serial,
    })
    if not container:
        print(f"device {name}: {st}, no address")
        continue
    iface = ensure("dcim/interfaces/", {"device": dev["id"], "name": "mgmt0"},
                   {"device": dev["id"], "name": "mgmt0", "type": "virtual", "status": status["Active"]})
    address = f"{ip_of(container)}/{MASK}"
    ip = ensure("ipam/ip-addresses/", {"address": address}, {
        "address": address, "namespace": namespace["id"], "status": status["Active"], "type": "host",
        "tenant": tenant_ids[code]})
    if not get("ipam/ip-address-to-interface/", ip_address=ip["id"], interface=iface["id"]):
        S.post(API + "ipam/ip-address-to-interface/",
               data=json.dumps({"ip_address": ip["id"], "interface": iface["id"]})).raise_for_status()
    S.patch(API + f"dcim/devices/{dev['id']}/", data=json.dumps({"primary_ip4": ip["id"]})).raise_for_status()
    print(f"device {name}: {address}")

# ------------------------------------------------- cables and circuits (the console's network map)
# The WAN port of each router ends on an IOH circuit; at ACME HQ the router's LAN port is cabled to the server.
provider = ensure("circuits/providers/", {"name": "Indosat Ooredoo Hutchison"}, {"name": "Indosat Ooredoo Hutchison"})
mpls = ensure("circuits/circuit-types/", {"name": "MPLS VPN"}, {"name": "MPLS VPN"})
by_name = {d["name"]: d for d in get("dcim/devices/", limit=100)}


def port(device, name, kind, speed, description):
    return ensure("dcim/interfaces/", {"device": by_name[device]["id"], "name": name}, {
        "device": by_name[device]["id"], "name": name, "type": kind, "status": status["Active"], "speed": speed,
        "description": description})


def cable(a_type, a_id, b_type, b_id):
    if get("dcim/cables/", termination_a_id=a_id) or get("dcim/cables/", termination_b_id=a_id):
        return
    r = S.post(API + "dcim/cables/", data=json.dumps({
        "termination_a_type": a_type, "termination_a_id": a_id, "termination_b_type": b_type, "termination_b_id": b_id,
        "status": status["Connected"]}))
    if r.status_code >= 400:
        raise SystemExit(f"cable failed: {r.status_code} {r.text}")


for device, code, site, cid, rate in (("jkt01-rtr01", "acme", "acme-hq", "IOH-MPLS-ACME-0001", 100000),
                                       ("ckr01-rtr01", "bankx", "bankx-dc", "IOH-MPLS-BANKX-0001", 1000000)):
    wan = port(device, "GigabitEthernet0/0/0", "1000base-t", 1000000, "WAN to IOH")
    circuit = ensure("circuits/circuits/", {"cid": cid}, {
        "cid": cid, "provider": provider["id"], "circuit_type": mpls["id"], "status": status["Active"],
        "tenant": tenant_ids[code], "commit_rate": rate})
    end = ensure("circuits/circuit-terminations/", {"circuit": circuit["id"], "term_side": "A"}, {
        "circuit": circuit["id"], "term_side": "A", "location": sites[site]["id"]})
    cable("dcim.interface", wan["id"], "circuits.circuittermination", end["id"])
    print(f"circuit {cid}: {device} GigabitEthernet0/0/0")
lan = port("jkt01-rtr01", "GigabitEthernet0/0/1", "1000base-t", 1000000, "LAN, server room")
eth0 = port("jkt01-srv01", "eth0", "1000base-t", 1000000, "")
cable("dcim.interface", lan["id"], "dcim.interface", eth0["id"])
print("cable: jkt01-rtr01 GigabitEthernet0/0/1 to jkt01-srv01 eth0")
