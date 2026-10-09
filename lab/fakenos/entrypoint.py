"""Start one FakeNOS Cisco IOS device whose 'show running-config' reflects env settings.

Environment:
  DEVICE_NAME   host name shown in the prompt and configuration (default lab-router)
  NTP_SERVERS   comma-separated NTP servers present in the running configuration
  LOG_HOSTS     comma-separated syslog servers
  USERNAME / PASSWORD  SSH login
"""
import os
import yaml
import fakenos
from fakenos import FakeNOS

name = os.environ.get("DEVICE_NAME", "lab-router")
ntp = [s for s in os.environ.get("NTP_SERVERS", "10.255.0.1,10.255.0.2").split(",") if s]
logs = [s for s in os.environ.get("LOG_HOSTS", "10.255.0.10").split(",") if s]
location = os.environ.get("SNMP_LOCATION", "IOH managed")

config_lines = [
    "Building configuration...", "", "Current configuration : 2048 bytes", "!",
    "! Last configuration change at 06:00:00 WIB Mon Oct 5 2026", "!", "version 17.9",
    "service timestamps log datetime msec", "!", f"hostname {name}", "!",
    "username netadmin privilege 15 secret 9 $9$abcdefghijklmnopqrstuv", "!",
    "interface GigabitEthernet0/0/0", " description WAN to Indosat", " ip address 10.10.10.1 255.255.255.252",
    "!", "interface GigabitEthernet0/0/1", " description LAN", " ip address 192.168.1.1 255.255.255.0", "!",
]
config_lines += [f"ntp server {s}" for s in ntp] + ["!"]
config_lines += [f"logging host {h}" for h in logs] + ["!"]
config_lines += [f"snmp-server community S3cretCommunity RO", f"snmp-server location {location}",
                 "snmp-server contact noc@indosatooredoo.com", "!",
                 "banner motd ^C", "Authorised access only. This device is managed by Indosat Ooredoo Hutchison.", "^C",
                 "!", "end"]
running = "\n".join(config_lines)

base = os.path.join(os.path.dirname(fakenos.__file__), "plugins", "nos", "platforms_yaml", "cisco_ios.yaml")
with open(base, encoding="utf-8") as handle:
    nos = yaml.safe_load(handle)
prompts = ["{base_prompt}>", "{base_prompt}#"]
for command in ("show running-config", "show run", "show startup-config"):
    nos["commands"][command] = {"output": running, "help": f"execute the command \"{command}\"", "prompt": prompts}
nos["commands"]["terminal width 511"] = {"output": "", "help": "terminal width", "prompt": prompts}
nos["commands"]["terminal length 0"] = {"output": "", "help": "terminal length", "prompt": prompts}
with open("/app/nos.yaml", "w", encoding="utf-8") as handle:
    yaml.safe_dump(nos, handle)

inventory = {
    "default": {"username": os.environ.get("USERNAME", "netadmin"),
                "password": os.environ.get("PASSWORD", "Lab-Device-Pass1"),
                "server": {"plugin": "ParamikoSshServer", "configuration": {"address": "0.0.0.0", "timeout": 1}}},
    "hosts": {name: {"port": 22, "platform": "cisco_ios", "nos": {"plugin": "/app/nos.yaml"}}},
}
print(f"Starting simulated Cisco IOS device {name} with NTP {ntp} and syslog {logs}", flush=True)
net = FakeNOS(inventory=inventory)
net.start()
import time
while True:
    time.sleep(3600)
