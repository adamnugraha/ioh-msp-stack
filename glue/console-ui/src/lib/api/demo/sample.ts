/*
 * Sample data for the preview build. Every customer, person, address and number here is made up;
 * the shapes are the ones the console's API returns, so the pages cannot tell the difference.
 */
import type {
  AuditEntry,
  Change,
  ComplianceRule,
  Device,
  DeviceRole,
  DeviceStatus,
  GlueStatus,
  Maintenance,
  Me,
  Problem,
  Role,
  SessionRow,
  Site,
  Task,
  Template,
  Ticket,
  Tier,
  ToolStatus,
  TopoLink,
} from "../types";
import { at, DAY, HOUR, iso, MIN, nextQuarter } from "./clock";

export const DOMAIN = "msp.example";
export function toolUrl(tool: string, path = ""): string {
  const host = ({ forgejo: "git", keycloak: "sso" } as Record<string, string>)[tool] ?? tool;
  return `https://${host}.${DOMAIN}${path}`;
}

// ------------------------------------------------------------------------------------- people
export interface Staff {
  name: string;
  email: string;
  roles: Role[];
}

export const STAFF = {
  dimas: { name: "Dimas Hartono", email: "dimas.hartono@ioh.example", roles: ["manager", "engineer", "automation"] },
  eko: { name: "Eko Prasetyo", email: "eko.prasetyo@ioh.example", roles: ["engineer"] },
  rina: { name: "Rina Saputri", email: "rina.saputri@ioh.example", roles: ["engineer", "automation"] },
  budi: { name: "Budi Santoso", email: "budi.santoso@ioh.example", roles: ["engineer", "automation"] },
  sari: { name: "Sari Wulandari", email: "sari.wulandari@ioh.example", roles: ["manager"] },
} satisfies Record<string, Staff>;

// ---------------------------------------------------------------------------------- customers
export interface DemoCustomer {
  code: string;
  name: string;
  tier: Tier;
  regulated: boolean;
  provisioning: "provisioned" | "pending" | "error";
  provisioningMessage: string;
  customerId: string;
  contactEmail: string;
  sites: Site[];
  projectId: number | null;
  hostgroupId: number | null;
  entityId: number | null;
  grafanaOrg: number | null;
  /** availability of the four months before last, oldest first */
  olderMonths: (number | null)[];
  month: { opened: number; solved: number; ownLate: number; resolveLate: number; mttrHours: number | null };
  /** how busy the network is through the day */
  traffic: { peakMbps: number; profile: "office" | "retail" | "hospital" | "plant" | "logistics" };
}

const site = (id: string, name: string, region: string): Site => ({ id, name, region });

export const CUSTOMERS: DemoCustomer[] = [
  {
    code: "bankx", name: "PT Bank Contoh Tbk", tier: "gold", regulated: true, provisioning: "provisioned",
    provisioningMessage: "All tools set up", customerId: "IOH-100871", contactEmail: "noc@bankcontoh.example",
    sites: [
      site("ckr01", "Bank Contoh DC Cikarang", "West Java"),
      site("btm01", "Bank Contoh DR Batam", "Riau Islands"),
      site("mdn01", "Branch Medan", "North Sumatra"),
      site("sby01", "Branch Surabaya", "East Java"),
      site("mks01", "Branch Makassar", "South Sulawesi"),
      site("dps01", "Branch Denpasar", "Bali"),
    ],
    projectId: 3, hostgroupId: 31, entityId: 7, grafanaOrg: 3,
    olderMonths: [99.982, 100, 99.991, 99.976],
    month: { opened: 21, solved: 18, ownLate: 1, resolveLate: 0, mttrHours: 2.6 },
    traffic: { peakMbps: 640, profile: "office" },
  },
  {
    code: "acme", name: "PT ACME Retail Indonesia", tier: "silver", regulated: false, provisioning: "provisioned",
    provisioningMessage: "All tools set up", customerId: "IOH-100234", contactEmail: "it@acme.example",
    sites: [
      site("jkt01", "ACME HQ Jakarta", "Jakarta"),
      site("bdg01", "ACME Store Bandung", "West Java"),
      site("bks01", "ACME Store Bekasi", "West Java"),
      site("ckr02", "ACME Warehouse Cikarang", "West Java"),
    ],
    projectId: 2, hostgroupId: 30, entityId: 6, grafanaOrg: 2,
    olderMonths: [99.871, 99.954, 99.802, 99.938],
    month: { opened: 17, solved: 14, ownLate: 1, resolveLate: 1, mttrHours: 5.1 },
    traffic: { peakMbps: 150, profile: "retail" },
  },
  {
    code: "medika", name: "RS Contoh Medika", tier: "gold", regulated: true, provisioning: "provisioned",
    provisioningMessage: "All tools set up", customerId: "IOH-101402", contactEmail: "it@rscontohmedika.example",
    sites: [site("jks01", "RS Contoh Medika Jakarta Selatan", "Jakarta"), site("dpk01", "Klinik Contoh Depok", "West Java")],
    projectId: 4, hostgroupId: 32, entityId: 8, grafanaOrg: null,
    olderMonths: [100, 99.996, 100, 99.989],
    month: { opened: 9, solved: 8, ownLate: 0, resolveLate: 0, mttrHours: 1.9 },
    traffic: { peakMbps: 380, profile: "hospital" },
  },
  {
    code: "logix", name: "PT Logistik Contoh", tier: "silver", regulated: false, provisioning: "provisioned",
    provisioningMessage: "All tools set up", customerId: "IOH-100977", contactEmail: "ops@logistikcontoh.example",
    sites: [
      site("ckr03", "Hub Cikarang", "West Java"),
      site("sby02", "Hub Surabaya", "East Java"),
      site("tpk01", "Kantor Tanjung Priok", "Jakarta"),
    ],
    projectId: 5, hostgroupId: 33, entityId: 9, grafanaOrg: 5,
    olderMonths: [99.731, 99.912, 99.655, 99.874],
    month: { opened: 12, solved: 9, ownLate: 1, resolveLate: 1, mttrHours: 6.8 },
    traffic: { peakMbps: 120, profile: "logistics" },
  },
  {
    code: "pabrik", name: "PT Pabrik Contoh", tier: "bronze", regulated: false, provisioning: "provisioned",
    provisioningMessage: "All tools set up", customerId: "IOH-101115", contactEmail: "it@pabrikcontoh.example",
    sites: [site("krw01", "Plant Karawang", "West Java")],
    projectId: 6, hostgroupId: 34, entityId: 10, grafanaOrg: null,
    olderMonths: [99.402, 99.611, 99.157, 99.528],
    month: { opened: 5, solved: 4, ownLate: 0, resolveLate: 0, mttrHours: 11.2 },
    traffic: { peakMbps: 60, profile: "plant" },
  },
  {
    code: "energi", name: "PT Energi Contoh", tier: "silver", regulated: false, provisioning: "error",
    provisioningMessage: "Semaphore did not answer when the customer's project was created (HTTP 502 Bad Gateway). Re-sync the customer.",
    customerId: "IOH-101530", contactEmail: "it@energicontoh.example",
    sites: [site("jkt02", "Energi Contoh Head Office", "Jakarta")],
    projectId: null, hostgroupId: 35, entityId: 11, grafanaOrg: null,
    olderMonths: [null, null, null, null],
    month: { opened: 1, solved: 0, ownLate: 0, resolveLate: 0, mttrHours: null },
    traffic: { peakMbps: 40, profile: "office" },
  },
];

// ------------------------------------------------------------------------------------ devices
interface DevSpec {
  name: string;
  role: DeviceRole;
  platform: string;
  model: string;
  ip?: string;
  serial: string;
  status?: DeviceStatus;
  monitoring?: boolean;
}

const d = (name: string, role: DeviceRole, platform: string, model: string, ip: string | undefined, serial: string,
  extra: Partial<DevSpec> = {}): DevSpec => ({ name, role, platform, model, ip, serial, ...extra });

const DEVICE_SPECS: Record<string, DevSpec[]> = {
  bankx: [
    d("ckr01-rtr01", "router", "Cisco IOS-XE", "C8300-2N2S-6T", "10.71.0.1", "FDO2611A0K1"),
    d("ckr01-rtr02", "router", "Cisco IOS-XE", "C8300-2N2S-6T", "10.71.0.2", "FDO2611A0K7"),
    d("ckr01-fw01", "firewall", "FortiOS", "FortiGate 200F", "10.71.0.11", "FG200FT923901451"),
    d("ckr01-fw02", "firewall", "FortiOS", "FortiGate 200F", "10.71.0.12", "FG200FT923901488"),
    d("ckr01-sw01", "switch", "Cisco NX-OS", "Nexus 93180YC-FX3", "10.71.0.21", "FLM2634080A"),
    d("ckr01-sw02", "switch", "Cisco NX-OS", "Nexus 93180YC-FX3", "10.71.0.22", "FLM2634081C"),
    d("ckr01-srv01", "server", "Linux", "Virtual machine", "10.71.0.51", "VM-BKX-0051"),
    d("ckr01-srv02", "server", "Linux", "Virtual machine", "10.71.0.52", "VM-BKX-0052"),
    d("btm01-rtr01", "router", "Cisco IOS-XE", "C8300-1N1S-6T", "10.71.8.1", "FDO2612B1Q3"),
    d("btm01-fw01", "firewall", "FortiOS", "FortiGate 100F", "10.71.8.11", "FG100FTK22011870"),
    d("btm01-sw01", "switch", "Cisco NX-OS", "Nexus 93108TC-FX3P", "10.71.8.21", "FLM2633070D"),
    d("mdn01-rtr01", "router", "Cisco IOS-XE", "C8200-1N-4T", "10.71.20.1", "FGL2601A2BC"),
    d("mdn01-sw01", "switch", "Cisco IOS", "C9200L-24T-4G", "10.71.20.2", "JAE2602X1AB"),
    d("sby01-rtr01", "router", "Cisco IOS-XE", "C8200-1N-4T", "10.71.21.1", "FGL2601A2DE"),
    d("sby01-sw01", "switch", "Cisco IOS", "C9200L-24T-4G", "10.71.21.2", "JAE2602X1CD"),
    d("mks01-rtr01", "router", "Cisco IOS-XE", "C8200-1N-4T", "10.71.22.1", "FGL2601A2FG"),
    d("mks01-sw01", "switch", "Cisco IOS", "C9200L-24T-4G", "10.71.22.2", "JAE2602X1EF"),
    d("dps01-rtr01", "router", "Cisco IOS-XE", "C8200-1N-4T", undefined, "FGL2604C7HJ", { status: "Planned" }),
    d("dps01-sw01", "switch", "Cisco IOS", "C9200L-24T-4G", undefined, "JAE2604Y2GH", { status: "Planned" }),
  ],
  acme: [
    d("jkt01-rtr01", "router", "Cisco IOS-XE", "C8200-1N-4T", "10.23.0.1", "FGL2601A1BC"),
    d("jkt01-fw01", "firewall", "FortiOS", "FortiGate 60F", "10.23.0.2", "FGT60FTK21047712"),
    d("jkt01-sw01", "switch", "Cisco IOS", "C9200L-24T-4G", "10.23.0.3", "JAE2602X0YZ"),
    d("jkt01-sw02", "switch", "Cisco IOS", "C9200L-48P-4G", "10.23.0.4", "JAE2602X0ZA"),
    d("jkt01-srv01", "server", "Linux", "Virtual machine", "10.23.0.10", "VM-ACM-0010"),
    d("jkt01-srv02", "server", "Windows", "Virtual machine", "10.23.0.11", "VM-ACM-0011", { monitoring: false }),
    d("bdg01-rtr01", "router", "RouterOS", "RB5009UG+S+IN", "10.23.10.1", "HE30AB1K2M3"),
    d("bdg01-sw01", "switch", "Cisco IOS", "C9200L-24T-4G", undefined, "JAE2602X0YB", { status: "Planned" }),
    d("bks01-rtr01", "router", "RouterOS", "RB5009UG+S+IN", "10.23.11.1", "HE30AB1K2Q8"),
    d("bks01-sw01", "switch", "AOS-CX", "Aruba CX 6100 24G", "10.23.11.2", "SG2ZKN301B"),
    d("ckr02-rtr01", "router", "Cisco IOS-XE", "C8200-1N-4T", "10.23.20.1", "FGL2603B0LM"),
    d("ckr02-sw01", "switch", "Cisco IOS", "C9200L-24T-4G", "10.23.20.2", "JAE2603X3MN"),
  ],
  medika: [
    d("jks01-rtr01", "router", "Cisco IOS-XE", "C8300-1N1S-6T", "10.14.0.1", "FDO2612B2R5"),
    d("jks01-fw01", "firewall", "FortiOS", "FortiGate 100F", "10.14.0.2", "FG100FTK22013395"),
    d("jks01-sw01", "switch", "AOS-CX", "Aruba CX 6300M", "10.14.0.3", "SG1ZKM2007"),
    d("jks01-sw02", "switch", "AOS-CX", "Aruba CX 6100 48G", "10.14.0.4", "SG2ZKN302F"),
    d("jks01-sw03", "switch", "AOS-CX", "Aruba CX 6100 48G", "10.14.0.5", "SG2ZKN302H"),
    d("jks01-srv01", "server", "Linux", "Virtual machine", "10.14.0.20", "VM-MDK-0020"),
    d("jks01-srv02", "server", "Linux", "Virtual machine", "10.14.0.21", "VM-MDK-0021"),
    d("dpk01-rtr01", "router", "Cisco IOS-XE", "C8200-1N-4T", "10.14.10.1", "FGL2603B1NP"),
    d("dpk01-sw01", "switch", "AOS-CX", "Aruba CX 6100 24G", "10.14.10.2", "SG2ZKN303K"),
  ],
  logix: [
    d("ckr03-rtr01", "router", "Huawei VRP", "AR6120", "10.97.0.1", "2102313TXL10M2000123"),
    d("ckr03-sw01", "switch", "Huawei VRP", "S5735-L24T4X", "10.97.0.2", "4E2253009876"),
    d("ckr03-srv01", "server", "Linux", "Virtual machine", "10.97.0.10", "VM-LGX-0010"),
    d("sby02-rtr01", "router", "Huawei VRP", "AR6120", "10.97.10.1", "2102313TXL10M2000456"),
    d("sby02-sw01", "switch", "Huawei VRP", "S5735-L24T4X", "10.97.10.2", "4E2253009911"),
    d("tpk01-rtr01", "router", "RouterOS", "RB4011iGS+RM", "10.97.20.1", "D4500C1E2F3"),
  ],
  pabrik: [
    d("krw01-rtr01", "router", "RouterOS", "CCR2004-16G-2S+", "10.11.0.1", "HD20AB7C8D9"),
    d("krw01-fw01", "firewall", "FortiOS", "FortiGate 40F", "10.11.0.2", "FGT40FTK21003318"),
    d("krw01-sw01", "switch", "TP-Link JetStream", "TL-SG3428", "10.11.0.3", "22410N8000123"),
    d("krw01-sw02", "switch", "TP-Link JetStream", "TL-SG3428", "10.11.0.4", "22410N8000188"),
    d("krw01-srv01", "server", "Windows", "PowerEdge T150", "10.11.0.10", "7XK2QJ3"),
  ],
  energi: [
    d("jkt02-rtr01", "router", "Cisco IOS-XE", "C8200-1N-4T", undefined, "FGL2605D2AB", { status: "Planned" }),
    d("jkt02-fw01", "firewall", "FortiOS", "FortiGate 60F", undefined, "FGT60FTK23001842", { status: "Planned" }),
    d("jkt02-sw01", "switch", "Cisco IOS", "C9200L-24T-4G", undefined, "JAE2605Y0CD", { status: "Planned" }),
  ],
};

/** What a device runs in Golden Config and Zabbix, for the device page. */
export const DEVICE_NOTES: Record<string, string> = {
  "ckr01-srv01": "Jump host for the bank's engineers",
  "ckr01-srv02": "Syslog collector",
  "jkt01-srv01": "Point-of-sale back end",
  "jkt01-srv02": "File server (monitored by the customer)",
  "jks01-srv01": "PACS gateway (imaging)",
  "jks01-srv02": "HL7 interface engine",
  "ckr03-srv01": "Warehouse management system",
  "krw01-srv01": "SCADA historian",
};

export const GC_PLATFORMS = new Set(["Cisco IOS", "Cisco IOS-XE", "Cisco NX-OS", "Cisco ASA", "FortiOS", "Junos", "Arista EOS",
  "RouterOS", "Huawei VRP", "AOS-CX", "Comware", "TP-Link JetStream"]);

const RULES: Record<string, string[]> = {
  default: ["aaa", "banner", "logging", "ntp", "snmp", "users"],
  FortiOS: ["admin", "logging", "ntp", "snmp", "system-global"],
  "Linux": [],
};

/** Golden Config rules that do not match, by device. */
const DRIFT: Record<string, { rule: string; missing: string[]; extra: string[]; ticket: number }> = {
  "btm01-fw01": {
    rule: "logging",
    missing: ["config log syslogd setting", "    set status enable", '    set server "10.255.0.10"', "    set mode reliable", "end"],
    extra: ["config log syslogd setting", "    set status disable", "end"],
    ticket: 1035,
  },
  "jkt01-sw01": {
    rule: "ntp",
    missing: ["ntp server 10.255.0.1 prefer", "ntp server 10.255.0.2"],
    extra: ["ntp server 0.id.pool.ntp.org"],
    ticket: 1037,
  },
};

const INTENDED: Record<string, string[]> = {
  aaa: ["aaa new-model", "aaa authentication login default group tacacs+ local", "aaa authorization exec default group tacacs+ local"],
  banner: ["banner login ^", "Authorised access only. Activity is logged.", "^"],
  logging: ["logging host 10.255.0.10", "logging trap informational", "logging source-interface Loopback0"],
  ntp: ["ntp server 10.255.0.1 prefer", "ntp server 10.255.0.2"],
  snmp: ["snmp-server location IOH managed", "snmp-server contact noc@ioh.example", "snmp-server community *** RO SNMP-ACL"],
  users: ["username netadmin privilege 15 secret 9 ***"],
  admin: ["config system admin", '    edit "netadmin"', '        set accprofile "super_admin"', "    next", "end"],
  "system-global": ["config system global", '    set hostname "<device>"', '    set timezone "Asia/Jakarta"', "end"],
};

export function complianceRules(device: Device): ComplianceRule[] {
  if (!device.check) return [];
  const names = RULES[device.platform] ?? RULES.default;
  return names.map((rule) => {
    const drift = DRIFT[device.name];
    if (drift && drift.rule === rule) return { rule, compliant: false, missing: drift.missing, extra: drift.extra };
    return { rule, compliant: true, missing: [], extra: [] };
  });
}

export function intendedLines(rule: string): string[] {
  return INTENDED[rule] ?? [];
}

// ------------------------------------------------------------------------------------ cabling
/* [a, a interface, b ("IOH" for a circuit to the IOH network), b interface, speed in Gbit/s, circuit id] */
type LinkSpec = [string, string, string, string, number, string?];

const LINK_SPECS: Record<string, LinkSpec[]> = {
  bankx: [
    ["ckr01-rtr01", "Gi0/0/0", "IOH", "PE ckr", 1, "BANKX-MPLS-CKR-01"],
    ["ckr01-rtr02", "Gi0/0/0", "IOH", "PE ckr", 1, "BANKX-MPLS-CKR-02"],
    ["ckr01-rtr01", "Gi0/0/1", "ckr01-fw01", "port1", 1],
    ["ckr01-rtr02", "Gi0/0/1", "ckr01-fw02", "port1", 1],
    ["ckr01-fw01", "ha", "ckr01-fw02", "ha", 1],
    ["ckr01-fw01", "port2", "ckr01-sw01", "Eth1/49", 10],
    ["ckr01-fw02", "port2", "ckr01-sw02", "Eth1/49", 10],
    ["ckr01-sw01", "Eth1/53", "ckr01-sw02", "Eth1/53", 100],
    ["ckr01-sw01", "Eth1/1", "ckr01-srv01", "eth0", 10],
    ["ckr01-sw02", "Eth1/1", "ckr01-srv02", "eth0", 10],
    ["btm01-rtr01", "Gi0/0/0", "IOH", "PE btm", 1, "BANKX-MPLS-BTM-01"],
    ["btm01-rtr01", "Gi0/0/1", "btm01-fw01", "port1", 1],
    ["btm01-fw01", "port2", "btm01-sw01", "Eth1/49", 10],
    ["mdn01-rtr01", "Gi0/0/0", "IOH", "PE mdn", 1, "BANKX-MPLS-MDN-01"],
    ["mdn01-rtr01", "Cellular0/2/0", "IOH", "LTE", 0.15, "BANKX-LTE-MDN-01"],
    ["mdn01-rtr01", "Gi0/0/1", "mdn01-sw01", "Gi1/1/1", 1],
    ["sby01-rtr01", "Gi0/0/0", "IOH", "PE sby", 1, "BANKX-MPLS-SBY-01"],
    ["sby01-rtr01", "Gi0/0/1", "sby01-sw01", "Gi1/1/1", 1],
    ["mks01-rtr01", "Gi0/0/0", "IOH", "PE mks", 1, "BANKX-MPLS-MKS-01"],
    ["mks01-rtr01", "Gi0/0/1", "mks01-sw01", "Gi1/1/1", 1],
    ["dps01-rtr01", "Gi0/0/1", "dps01-sw01", "Gi1/1/1", 1],
  ],
  acme: [
    ["jkt01-rtr01", "Gi0/0/0", "IOH", "PE jkt", 1, "ACME-DIA-JKT-01"],
    ["jkt01-rtr01", "Gi0/0/1", "jkt01-fw01", "wan1", 1],
    ["jkt01-fw01", "internal1", "jkt01-sw01", "Gi1/1/1", 1],
    ["jkt01-sw01", "Gi1/1/2", "jkt01-sw02", "Gi1/1/1", 1],
    ["jkt01-sw01", "Gi1/0/1", "jkt01-srv01", "eth0", 1],
    ["jkt01-sw01", "Gi1/0/2", "jkt01-srv02", "Ethernet0", 1],
    ["bdg01-rtr01", "ether1", "IOH", "BB", 1, "ACME-BB-BDG-01"],
    ["bdg01-rtr01", "ether2", "bdg01-sw01", "Gi1/0/24", 1],
    ["bks01-rtr01", "ether1", "IOH", "BB", 1, "ACME-BB-BKS-01"],
    ["bks01-rtr01", "ether2", "bks01-sw01", "1/1/24", 1],
    ["ckr02-rtr01", "Gi0/0/0", "IOH", "BB", 1, "ACME-BB-CKR-01"],
    ["ckr02-rtr01", "Gi0/0/1", "ckr02-sw01", "Gi1/1/1", 1],
  ],
  medika: [
    ["jks01-rtr01", "Gi0/0/0", "IOH", "PE jks", 1, "MEDIKA-MPLS-JKS-01"],
    ["jks01-rtr01", "Gi0/0/1", "jks01-fw01", "port1", 1],
    ["jks01-fw01", "port2", "jks01-sw01", "1/1/49", 10],
    ["jks01-sw01", "1/1/50", "jks01-sw02", "1/1/49", 10],
    ["jks01-sw01", "1/1/51", "jks01-sw03", "1/1/49", 10],
    ["jks01-sw01", "1/1/1", "jks01-srv01", "eth0", 10],
    ["jks01-sw01", "1/1/2", "jks01-srv02", "eth0", 10],
    ["dpk01-rtr01", "Gi0/0/0", "IOH", "PE dpk", 1, "MEDIKA-MPLS-DPK-01"],
    ["dpk01-rtr01", "Gi0/0/1", "dpk01-sw01", "1/1/25", 1],
  ],
  logix: [
    ["ckr03-rtr01", "GE0/0/0", "IOH", "PE ckr", 1, "LOGIX-MPLS-CKR-01"],
    ["ckr03-rtr01", "GE0/0/1", "ckr03-sw01", "GE0/0/24", 1],
    ["ckr03-sw01", "GE0/0/1", "ckr03-srv01", "eth0", 1],
    ["sby02-rtr01", "GE0/0/0", "IOH", "PE sby", 1, "LOGIX-MPLS-SBY-01"],
    ["sby02-rtr01", "GE0/0/1", "sby02-sw01", "GE0/0/24", 1],
    ["tpk01-rtr01", "ether1", "IOH", "BB", 1, "LOGIX-BB-TPK-01"],
  ],
  pabrik: [
    ["krw01-rtr01", "sfp-sfpplus1", "IOH", "PE krw", 1, "PABRIK-DIA-KRW-01"],
    ["krw01-rtr01", "ether2", "krw01-fw01", "wan1", 1],
    ["krw01-fw01", "lan1", "krw01-sw01", "1/0/25", 1],
    ["krw01-sw01", "1/0/26", "krw01-sw02", "1/0/24", 1],
    ["krw01-sw01", "1/0/1", "krw01-srv01", "NIC1", 1],
  ],
  energi: [
    ["jkt02-rtr01", "Gi0/0/1", "jkt02-fw01", "port1", 1],
    ["jkt02-fw01", "port2", "jkt02-sw01", "Gi1/1/1", 1],
  ],
};

/** Ports without a cable, shown on the device page so the list looks like the real one. */
export const SPARE_PORTS: Record<string, string[]> = {
  router: ["Gi0/0/2", "Gi0/0/3"],
  switch: ["Gi1/0/12", "Gi1/0/13", "Gi1/0/14"],
  firewall: ["port3", "port4"],
  server: [],
  wireless: [],
};

// --------------------------------------------------------------------------- availability history
export interface Outage {
  customer: string;
  device: string;
  start: number;
  /** null while it lasts */
  minutes: number | null;
  /** an availability problem of High or worse outside maintenance: it counts against the agreement */
  counted: boolean;
  cause: string;
}

function outages(now: number): Outage[] {
  const o = (customer: string, device: string, start: number, minutes: number | null, cause: string, counted = true): Outage =>
    ({ customer, device, start, minutes, counted, cause });
  return [
    o("acme", "bks01-rtr01", now - 23 * MIN, null, "now"),
    o("acme", "bks01-sw01", now - 23 * MIN, null, "now", false),
    o("acme", "bks01-rtr01", at(now, -14, "10:12"), 50, "Power cut at the store (ticket #1008)"),
    o("acme", "bks01-sw01", at(now, -14, "10:12"), 50, "Power cut at the store (ticket #1008)", false),
    o("acme", "bks01-rtr01", at(now, -26, "16:40"), 35, "Power cut at the store (ticket #962)"),
    o("bankx", "mks01-rtr01", at(now, -8, "03:20"), 12, "Router restarted after a power dip (ticket #1019)"),
    o("medika", "jks01-rtr01", at(now, -35, "21:05"), 20, "Carrier maintenance overran (ticket #941)"),
    o("medika", "jks01-sw02", now - 38 * MIN, null, "maintenance", false),
    o("logix", "ckr03-rtr01", at(now, -6, "14:05"), 185, "Power failure at Hub Cikarang (ticket #1012)"),
    o("logix", "ckr03-sw01", at(now, -6, "14:05"), 185, "Power failure at Hub Cikarang (ticket #1012)", false),
    o("logix", "ckr03-rtr01", at(now, -3, "08:30"), 55, "Router crashed, firmware bug (ticket #1027)"),
    o("logix", "sby02-rtr01", at(now, -22, "11:15"), 70, "Fibre cut near the hub (ticket #977)"),
    o("pabrik", "krw01-rtr01", at(now, -11, "07:40"), 130, "Plant power shutdown overran (ticket #1003)"),
    o("pabrik", "krw01-rtr01", at(now, -2, "19:00"), 60, "Router lost power, UPS battery flat (ticket #1032)"),
    o("pabrik", "krw01-rtr01", at(now, -39, "09:00"), 300, "Planned plant shutdown, not announced (ticket #921)"),
  ];
}

// --------------------------------------------------------------------------------- the state
export interface DemoState {
  now: number;
  me: Me;
  tools: ToolStatus[];
  customers: DemoCustomer[];
  devices: Device[];
  links: TopoLink[];
  outages: Outage[];
  problems: Problem[];
  tickets: Ticket[];
  changes: Change[];
  maintenance: Maintenance[];
  templates: Record<string, Template[]>;
  tasks: Task[];
  audit: AuditEntry[];
  sessions: SessionRow[];
  glue: GlueStatus;
  ids: { ticket: number; change: number; task: number; audit: number; maintenance: number; job: number; event: number };
}

export function meFor(staff: Staff, now: number): Me {
  return {
    email: staff.email,
    name: staff.name,
    roles: staff.roles,
    canAct: staff.roles.includes("engineer"),
    canRun: staff.roles.includes("automation"),
    isManager: staff.roles.includes("manager"),
    csrf: "sample",
    sessionEnds: iso(now + 8 * HOUR),
    accountUrl: toolUrl("keycloak", "/realms/msp/account"),
    timezone: "Asia/Jakarta",
    platformDomain: DOMAIN,
  };
}

function tools(): ToolStatus[] {
  const kc = (label: string, mapping: string, method: "oidc" | "saml" = "oidc") =>
    ({ method, label, mapping, enabled: true });
  return [
    { tool: "Nautobot", url: toolUrl("nautobot"), purpose: "Source of truth: customers, sites, devices, addresses, cables", ok: true,
      version: "3.2.6", ms: 142, detail: "3.2.6", signIn: kc("Keycloak, OpenID Connect", "msp-engineer: MSP engineers group (change); msp-manager: read only") },
    { tool: "Zabbix", url: toolUrl("zabbix"), purpose: "Monitoring, problems, availability agreements, maintenance", ok: true,
      version: "7.0.31", ms: 38, detail: "7.0.31", signIn: kc("Keycloak, SAML", "msp-engineer: MSP engineers user group; msp-manager: MSP managers (read only)", "saml") },
    { tool: "GLPI", url: toolUrl("glpi"), purpose: "Tickets, changes, service levels, assets", ok: true,
      version: "11.0.11", ms: 96, detail: "11.0.11", signIn: kc("Keycloak, OpenID Connect (plugin)", "msp-engineer: Technician profile; msp-manager: Supervisor profile") },
    { tool: "Semaphore", url: toolUrl("semaphore"), purpose: "Ansible playbooks per customer project", ok: true,
      version: "2.19.14", ms: 22, detail: "2.19.14", signIn: kc("Keycloak, OpenID Connect", "msp-automation: task runner in every customer project") },
    { tool: "Forgejo", url: toolUrl("forgejo"), purpose: "Configuration backups, playbooks, Golden Config templates", ok: true,
      version: "12.0.4", ms: 31, detail: "12.0.4", signIn: kc("Keycloak, OpenID Connect", "msp-engineer: read on backups; msp-automation: write on playbooks") },
    { tool: "Grafana", url: toolUrl("grafana"), purpose: "Customer dashboards (optional)", ok: true,
      version: "13.2.3", ms: 45, detail: "13.2.3", signIn: kc("Keycloak, OpenID Connect", "msp-manager: Viewer; msp-engineer: Editor, one organisation per customer") },
    { tool: "Keycloak", url: toolUrl("keycloak"), purpose: "Staff accounts, roles and single sign-on", ok: true,
      version: "26.8.0", ms: 27, detail: "26.8.0", signIn: { method: "oidc", label: "Identity provider for every tool", mapping: "Roles: msp-manager, msp-engineer, msp-automation", enabled: true } },
  ];
}

function devices(now: number): Device[] {
  const out: Device[] = [];
  for (const c of CUSTOMERS) {
    for (const spec of DEVICE_SPECS[c.code]) {
      const status = spec.status ?? "Active";
      const ip = spec.ip ?? "";
      const siteName = c.sites.find((s) => spec.name.startsWith(s.id))?.name ?? "";
      const network = spec.role !== "server";
      const gcScope = status === "Active" && Boolean(ip) && network && GC_PLATFORMS.has(spec.platform);
      const monitoring = spec.monitoring ?? true;
      const monitored = status === "Active" && monitoring && Boolean(ip);
      const hostid = String(10500 + out.length);
      const drift = DRIFT[spec.name];
      const backupFailed = spec.name === "bdg01-rtr01";
      const night = at(now, now - at(now, 0, "02:10") > 0 ? 0 : -1, "02:10");
      out.push({
        id: `nb-${spec.name}`,
        name: spec.name,
        serial: spec.serial,
        status,
        role: spec.role,
        platform: spec.platform,
        model: spec.model,
        site: siteName,
        customer: c.code,
        ip,
        gcScope,
        monitoring,
        driftTicket: drift?.ticket ?? null,
        host: monitored ? { hostid, state: "up", error: "", maintenance: false } : null,
        check: gcScope
          ? { rules: (RULES[spec.platform] ?? RULES.default).length, failed: drift ? [drift.rule] : [], checked: iso(night + 6 * MIN) }
          : null,
        backup: gcScope
          ? {
              lastSuccess: iso(backupFailed ? night - 4 * DAY : night + (out.length % 7) * MIN),
              lastAttempt: iso(night + (out.length % 7) * MIN),
            }
          : null,
        links: {
          nautobot: toolUrl("nautobot", `/dcim/devices/nb-${spec.name}/`),
          zabbix: monitored ? toolUrl("zabbix", `/zabbix.php?action=latest.view&hostids%5B%5D=${hostid}`) : undefined,
          backups: gcScope ? toolUrl("forgejo", `/msp/backups-${c.code}/src/branch/main/${slug(siteName)}/${spec.name}.cfg`) : undefined,
          driftTicket: drift ? toolUrl("glpi", `/front/ticket.form.php?id=${drift.ticket}`) : undefined,
        },
      });
    }
  }
  return out;
}

export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

function links(): TopoLink[] {
  const out: TopoLink[] = [];
  for (const c of CUSTOMERS) {
    for (const [a, aIf, b, bIf, gbps, circuit] of LINK_SPECS[c.code]) {
      out.push({
        id: `cable-${out.length + 1}`,
        a, aInterface: aIf,
        b: b === "IOH" ? `ioh-${c.code}` : b,
        bInterface: bIf,
        speed: gbps * 1e9,
        state: "up",
        kind: circuit ? "circuit" : "cable",
        circuit: circuit ?? null,
      });
    }
  }
  return out;
}

function problems(now: number): Problem[] {
  const p = (eventid: string, customer: string, device: string, name: string, severity: Problem["severity"], startedMin: number,
    opdata: string, ticketId: number | null, updates: Problem["updates"] = [], suppressed = false): Problem => {
    const c = CUSTOMERS.find((x) => x.code === customer)!;
    const spec = DEVICE_SPECS[customer].find((x) => x.name === device)!;
    return {
      eventid, name, severity, started: iso(now - startedMin * MIN), acknowledged: updates.length > 0, suppressed,
      customer, customerName: c.name, device, site: c.sites.find((s) => spec.name.startsWith(s.id))?.name ?? "",
      ticketId, ticketLink: ticketId ? toolUrl("glpi", `/front/ticket.form.php?id=${ticketId}`) : null, opdata, updates,
      link: toolUrl("zabbix", `/tr_events.php?triggerid=${23000 + Number(eventid) % 1000}&eventid=${eventid}`),
    };
  };
  const u = (who: Staff, minAgo: number, message: string) => ({ at: iso(now - minAgo * MIN), who: who.name, message });
  return [
    p("40817", "acme", "bks01-rtr01", "Unavailable by ICMP ping", 4, 23, "Loss: 100 %", 1047),
    p("40819", "medika", "jks01-sw02", "Unavailable by ICMP ping", 4, 38, "Loss: 100 %", null, [], true),
    p("40809", "bankx", "ckr01-fw01", "FortiGate: High CPU utilization (over 90% for 5m)", 3, 72, "CPU: 93 %", 1046),
    p("40821", "medika", "jks01-srv01", "FS [/var/lib/pacs]: Space is critically low (used > 95%)", 3, 48, "Used: 96.1 %", 1048),
    p("40788", "bankx", "mdn01-rtr01", "Interface Gi0/0/0(MPLS primary): Link down", 3, 130, "Current state: down", 1041,
      [u(STAFF.rina, 115, "Carrier fault raised, reference C-55102. The branch runs on the LTE backup.")]),
    p("40812", "pabrik", "krw01-sw02", "Interface 1/0/24: High error rate (>2 for 5m)", 2, 55, "Errors: 14 per minute", null),
    p("40795", "acme", "jkt01-rtr01", "High ICMP ping response time", 2, 185, "Response time: 186 ms", 1044,
      [u(STAFF.eko, 160, "Peak-hour congestion on the uplink. Watching it, diagnostics running.")]),
    p("40766", "logix", "ckr03-sw01", "Huawei: High memory utilization (>90% for 5m)", 2, 27 * 60, "Memory: 92 %", 1043,
      [u(STAFF.rina, 24 * 60, "Known memory leak in this firmware. Upgrade to be planned with the customer.")]),
  ];
}

function tickets(now: number): Ticket[] {
  const t = (id: number, customer: string, name: string, type: Ticket["type"], status: Ticket["status"], priority: Ticket["priority"],
    openedMin: number, dueMin: number | null, technician: string, source: Ticket["source"], device: string | null = null,
    group = "NOC Level 1"): Ticket => ({
    id, name, type, status, priority, customer, opened: iso(now - openedMin * MIN),
    due: dueMin === null ? null : iso(now + dueMin * MIN), late: dueMin !== null && dueMin < 0, group, technician, source, device,
    link: toolUrl("glpi", `/front/ticket.form.php?id=${id}`),
  });
  return [
    t(1048, "medika", "jks01-srv01: FS [/var/lib/pacs] space critically low", "Incident", "New", 4, 47, 193, "", "Monitoring", "jks01-srv01"),
    t(1047, "acme", "bks01-rtr01: Unavailable by ICMP ping", "Incident", "New", 4, 22, 458, "", "Monitoring", "bks01-rtr01"),
    t(1046, "bankx", "ckr01-fw01: High CPU utilization", "Incident", "Assigned", 3, 71, 169, STAFF.eko.name, "Monitoring", "ckr01-fw01"),
    t(1044, "acme", "jkt01-rtr01: High ICMP ping response time", "Incident", "Assigned", 3, 184, 296, STAFF.eko.name, "Monitoring", "jkt01-rtr01"),
    t(1043, "logix", "ckr03-sw01: High memory utilization", "Incident", "Waiting", 2, 27 * 60 - 1, -19 * 60, STAFF.rina.name, "Monitoring", "ckr03-sw01"),
    t(1041, "bankx", "mdn01-rtr01: Interface Gi0/0/0 (MPLS primary) link down", "Incident", "Waiting", 4, 129, 111, STAFF.rina.name, "Monitoring", "mdn01-rtr01"),
    t(1040, "energi", "Customer set-up: head office network devices", "Request", "New", 3, 26 * 60, 3 * 24 * 60, "", "MSP Console", null, "Service desk"),
    t(1039, "bankx", "New VLAN for the Makassar branch ATM network", "Request", "Planned", 3, 2 * 24 * 60, 2 * 24 * 60, STAFF.budi.name, "Email", "mks01-sw01"),
    t(1037, "acme", "Configuration drift on jkt01-sw01: ntp", "Incident", "Assigned", 2, 32 * 60, 16 * 60, STAFF.budi.name, "Configuration drift", "jkt01-sw01"),
    t(1035, "bankx", "Configuration drift on btm01-fw01: logging", "Incident", "Assigned", 3, 30 * 60, -2 * 60, STAFF.budi.name, "Configuration drift", "btm01-fw01"),
    t(1033, "medika", "Connect the new CT scanner to imaging VLAN 40", "Request", "Planned", 3, 3 * 24 * 60, 24 * 60, STAFF.rina.name, "Email", "jks01-sw02"),
    t(1031, "logix", "Wi-Fi slow in the Hub Surabaya loading area", "Request", "Assigned", 3, 52 * 60, -5 * 60, STAFF.eko.name, "Phone", "sby02-sw01"),
    t(1030, "acme", "Point-of-sale terminals slow at ACME Store Bandung", "Incident", "Waiting", 3, 9 * 60, -60, STAFF.budi.name, "Phone", "bdg01-rtr01"),
    t(1029, "pabrik", "Replace the cable on krw01-sw02 port 24", "Request", "Waiting", 2, 24 * 60, 2 * 24 * 60, STAFF.eko.name, "MSP Console", "krw01-sw02"),
  ];
}

function changes(now: number): Change[] {
  const c = (id: number, customer: string, name: string, status: Change["status"], category: string, template: string | null,
    start: number | null, minutes: number, risk: Change["risk"], devices: string[], requester: Staff,
    maintenanceId: string | null = null): Change => ({
    id, name, status, customer, category, template, plannedStart: start === null ? null : iso(start),
    plannedEnd: start === null ? null : iso(start + minutes * MIN), risk, devices, requester: requester.name, maintenanceId,
    link: toolUrl("glpi", `/front/change.form.php?id=${id}`),
  });
  const std = "Managed infrastructure > Standard change";
  return [
    c(62, "bankx", "FortiOS 7.4.8 upgrade on ckr01-fw01 and ckr01-fw02", "Accepted", std, null, nextQuarter(now, 5 * 60), 90, "Medium",
      ["ckr01-fw01", "ckr01-fw02"], STAFF.budi, "m-91"),
    c(61, "medika", "Move the imaging modalities to VLAN 40 on jks01-sw02", "Accepted", std, null, now - 45 * MIN, 120, "Medium",
      ["jks01-sw02"], STAFF.rina, "m-90"),
    c(60, "acme", "Bounce interface Gi1/0/12 on jkt01-sw01 (point-of-sale port stuck)", "Approval", `${std} > Bounce interface`,
      "Bounce interface", nextQuarter(now, 20 * 60), 15, "Low", ["jkt01-sw01"], STAFF.budi),
    c(59, "logix", "Linux patching on ckr03-srv01 (October)", "Accepted", `${std} > Linux patching`, "Linux patching",
      at(now, 2, "01:00"), 120, "Low", ["ckr03-srv01"], STAFF.eko),
    c(58, "bankx", "Collect diagnostics on the branch routers", "Applied", `${std} > Collect diagnostics`, "Collect diagnostics",
      now - 26 * HOUR, 45, "Low", ["mdn01-rtr01", "sby01-rtr01", "mks01-rtr01"], STAFF.rina, "m-88"),
    c(57, "pabrik", "Replace krw01-sw02 with a managed spare", "New", std, null, null, 60, "Medium", ["krw01-sw02"], STAFF.eko),
  ];
}

function maintenance(now: number): Maintenance[] {
  const m = (id: string, customer: string, name: string, devices: string[] | null, start: number, minutes: number,
    collectData: boolean, changeId: number | null, by: Staff): Maintenance => {
    const end = start + minutes * MIN;
    return {
      id, name, customer, devices, start: iso(start), end: iso(end), collectData, changeId, createdBy: by.name,
      state: now < start ? "upcoming" : now > end ? "ended" : "active",
      link: toolUrl("zabbix", `/zabbix.php?action=maintenance.list&filter_name=${encodeURIComponent(name)}`),
    };
  };
  const ch62 = nextQuarter(now, 5 * 60);
  return [
    m("m-90", "medika", "Change #61: imaging VLAN move", ["jks01-sw02"], now - 45 * MIN, 120, true, 61, STAFF.rina),
    m("m-91", "bankx", "Change #62: FortiOS upgrade", ["ckr01-fw01", "ckr01-fw02"], ch62 - 15 * MIN, 120, true, 62, STAFF.budi),
    m("m-89", "acme", "Store Bandung renovation (customer works)", ["bdg01-rtr01"], at(now, 3, "08:00"), 8 * 60, false, null, STAFF.eko),
    m("m-88", "bankx", "Change #58: branch diagnostics", ["mdn01-rtr01", "sby01-rtr01", "mks01-rtr01"], now - 26 * HOUR, 45, true, 58, STAFF.rina),
  ];
}

function templates(): Record<string, Template[]> {
  const base: Template[] = [
    { id: 1, name: "Collect diagnostics", description: "Read-only: gathers show commands or system facts and stores them in the task log.", limit: true, changeOnly: false },
    { id: 2, name: "Bounce interface", description: "Shuts and re-enables one interface. Needs an approved change.", limit: true, changeOnly: true },
    { id: 3, name: "Linux patching", description: "Applies security updates to Linux servers. Needs an approved change.", limit: true, changeOnly: true },
  ];
  const out: Record<string, Template[]> = {};
  for (const c of CUSTOMERS) {
    if (!c.projectId) continue;
    out[c.code] = base.map((t) => ({ ...t }));
  }
  out.bankx.push({ id: 4, name: "Quarterly access review", description: "Exports local accounts and privilege levels from every device of the bank.", limit: false, changeOnly: false });
  return out;
}

function tasks(now: number): Task[] {
  const t = (id: number, customer: string, template: string, status: Task["status"], minAgo: number, runMin: number,
    devices: string[], by: Staff | string, item: string, message: string): Task => {
    const c = CUSTOMERS.find((x) => x.code === customer)!;
    return {
      id, template, status, created: iso(now - minAgo * MIN), finished: status === "running" ? null : iso(now - (minAgo - runMin) * MIN),
      message, devices, by: typeof by === "string" ? by : by.name, item, customer,
      link: toolUrl("semaphore", `/project/${c.projectId}/history?t=${id}`),
    };
  };
  return [
    t(320, "bankx", "Collect diagnostics", "success", 110, 3, ["mdn01-rtr01"], STAFF.rina, "Ticket #1041",
      "Capture interface and LTE state after the MPLS link went down"),
    t(318, "acme", "Collect diagnostics", "success", 150, 2, ["jkt01-rtr01"], STAFF.eko, "Ticket #1044",
      "Interface counters and queueing during the latency peak"),
    t(316, "bankx", "Collect diagnostics", "success", 26 * 60, 6, ["mdn01-rtr01", "sby01-rtr01", "mks01-rtr01"], "Change #58 (accepted)",
      "Change #58", "Started by msp-glue when change #58 was accepted"),
    t(314, "logix", "Collect diagnostics", "success", 24 * 60 - 10, 2, ["ckr03-sw01"], STAFF.rina, "Ticket #1043",
      "Memory by process before the upgrade discussion"),
    t(312, "acme", "Linux patching", "success", 3 * 24 * 60, 18, ["jkt01-srv01"], STAFF.rina, "Change #55", "October security updates"),
    t(309, "bankx", "Bounce interface", "success", 5 * 24 * 60, 1, ["sby01-sw01"], STAFF.budi, "Change #54", "Port Gi1/0/7 stuck in err-disabled"),
    t(306, "medika", "Linux patching", "success", 12 * 24 * 60, 22, ["jks01-srv02"], STAFF.rina, "Change #52", "October security updates"),
    t(305, "acme", "Collect diagnostics", "error", 14 * 24 * 60 - 20, 1, ["bks01-rtr01"], STAFF.eko, "Ticket #1008",
      "Logs after the store outage (device unreachable)"),
    t(302, "pabrik", "Collect diagnostics", "success", 9 * 24 * 60, 2, ["krw01-rtr01"], STAFF.eko, "Ticket #1003", "Uptime and logs after the power cut"),
  ];
}

function audit(now: number): AuditEntry[] {
  let id = 400;
  const e = (minAgo: number, who: Staff | { name: string; email: string }, action: string, customer: string, target: string,
    ticket: string, reason: string, outcome: AuditEntry["outcome"], result = "", address = "203.0.113.24"): AuditEntry => ({
    id: id--, at: iso(now - minAgo * MIN), email: who.email, name: who.name, action, customer, target, ticket, reason, outcome,
    result, address, updated: outcome === "started" || outcome === "queued" ? null : iso(now - minAgo * MIN + 2 * MIN),
  });
  return [
    e(20, STAFF.dimas, "sign-in", "", "", "", "manager, engineer, automation", "done"),
    e(50, STAFF.rina, "maintenance", "medika", "jks01-sw02, 120 min, data collection on", "Change #61", "Imaging VLAN move", "done",
      "Zabbix maintenance 'Change #61: imaging VLAN move' created.", "198.51.100.17"),
    e(110, STAFF.rina, "playbook", "bankx", "Collect diagnostics on mdn01-rtr01", "Ticket #1041",
      "Capture interface and LTE state after the MPLS link went down", "success",
      "Semaphore task #320 finished with status success; the result is on Ticket #1041.", "198.51.100.17"),
    e(115, STAFF.rina, "acknowledge", "bankx", "mdn01-rtr01: Interface Gi0/0/0(MPLS primary): Link down", "Ticket #1041",
      "Carrier fault raised, reference C-55102. The branch runs on the LTE backup.", "done", "", "198.51.100.17"),
    e(150, STAFF.eko, "playbook", "acme", "Collect diagnostics on jkt01-rtr01", "Ticket #1044",
      "Interface counters and queueing during the latency peak", "success",
      "Semaphore task #318 finished with status success; the result is on Ticket #1044.", "198.51.100.40"),
    e(160, STAFF.eko, "acknowledge", "acme", "jkt01-rtr01: High ICMP ping response time", "Ticket #1044",
      "Peak-hour congestion on the uplink. Watching it, diagnostics running.", "done", "", "198.51.100.40"),
    e(4 * 60, STAFF.eko, "configuration check", "acme", "PT ACME Retail Indonesia", "", "", "errors",
      "Golden Config finished with errors: bdg01-rtr01 could not be backed up (authentication failed). Drift tickets update queued on msp-glue (job 4519).",
      "198.51.100.40"),
    e(5 * 60, STAFF.dimas, "re-sync", "energi", "PT Energi Contoh", "", "", "failed",
      "tenant energi: failed (Semaphore: project create failed: HTTP 502 Bad Gateway); devices energi: done (3 devices, all Planned)"),
    e(6 * 60, STAFF.budi, "investigation note", "acme", "bdg01-rtr01: point-of-sale terminals slow", "Ticket #1030",
      "Draft cause checked and posted", "done", "Private follow-up added to Ticket #1030.", "198.51.100.52"),
    e(6 * 60 + 30, STAFF.sari, "sign-in", "", "", "", "manager", "done", "", "198.51.100.88"),
    e(22 * 60, { name: "", email: "intern.noc@ioh.example" }, "sign-in refused", "", "", "", "no MSP Console role", "refused", "", "198.51.100.91"),
    e(24 * 60 - 10, STAFF.rina, "playbook", "logix", "Collect diagnostics on ckr03-sw01", "Ticket #1043", "Memory by process before the upgrade discussion",
      "success", "Semaphore task #314 finished with status success; the result is on Ticket #1043.", "198.51.100.17"),
    e(24 * 60, STAFF.rina, "acknowledge", "logix", "ckr03-sw01: Huawei: High memory utilization (>90% for 5m)", "Ticket #1043",
      "Known memory leak in this firmware. Upgrade to be planned with the customer.", "done", "", "198.51.100.17"),
    e(26 * 60, STAFF.dimas, "ticket", "energi", "Request: Customer set-up: head office network devices", "Ticket #1040", "", "done",
      "GLPI ticket #1040 created."),
    e(27 * 60, STAFF.dimas, "add device", "energi", "jkt02-sw01 (Planned) at Energi Contoh Head Office", "", "", "done",
      "Added to Nautobot. msp-glue provisions it once it is Active with an address."),
    e(27 * 60 + 2, STAFF.dimas, "add device", "energi", "jkt02-fw01 (Planned) at Energi Contoh Head Office", "", "", "done",
      "Added to Nautobot. msp-glue provisions it once it is Active with an address."),
    e(27 * 60 + 4, STAFF.dimas, "add device", "energi", "jkt02-rtr01 (Planned) at Energi Contoh Head Office", "", "", "done",
      "Added to Nautobot. msp-glue provisions it once it is Active with an address."),
    e(2 * 24 * 60, STAFF.budi, "change", "acme", "Bounce interface Gi1/0/12 on jkt01-sw01 (point-of-sale port stuck)", "Change #60", "", "done",
      "GLPI change #60 created, waiting for approval.", "198.51.100.52"),
    e(3 * 24 * 60, STAFF.rina, "playbook", "acme", "Linux patching on jkt01-srv01", "Change #55", "October security updates", "success",
      "Semaphore task #312 finished with status success; the result is on Change #55.", "198.51.100.17"),
    e(3 * 24 * 60 + 40, STAFF.eko, "re-sync", "acme", "PT ACME Retail Indonesia", "", "", "done",
      "tenant acme: done (no changes); devices acme: done (12 devices, 0 changed)", "198.51.100.40"),
  ];
}

function sessions(now: number): SessionRow[] {
  const s = (who: Staff, sinceMin: number, lastMin: number, address: string): SessionRow =>
    ({ email: who.email, name: who.name, roles: who.roles, since: iso(now - sinceMin * MIN), lastSeen: iso(now - lastMin * MIN), address });
  return [
    s(STAFF.dimas, 20, 0, "203.0.113.24"),
    s(STAFF.rina, 3 * 60, 2, "198.51.100.17"),
    s(STAFF.eko, 5 * 60, 6, "198.51.100.40"),
    s(STAFF.sari, 6 * 60 + 30, 58, "198.51.100.88"),
  ];
}

function glue(now: number): GlueStatus {
  const j = (id: number, name: string, status: "done" | "failed", minAgo: number, seconds: number, result: string, by = "schedule") =>
    ({ id, name, status, started: iso(now - minAgo * MIN), finished: iso(now - minAgo * MIN + seconds * 1000), result, by });
  return {
    queue: 0,
    lastReconcile: iso(now - 4 * MIN),
    lastError: { at: iso(now - 5 * HOUR), message: "tenant energi: Semaphore project create failed: HTTP 502 Bad Gateway" },
    jobs: [
      j(4523, "reconcile", "done", 4, 38, "54 devices checked, nothing to change"),
      j(4522, "webhook device jks01-sw02", "done", 52, 2, "Zabbix maintenance noted, host unchanged", "Nautobot"),
      j(4521, "reconcile", "done", 64, 41, "54 devices checked, nothing to change"),
      j(4519, "compliance", "done", 4 * 60 - 6, 12, "2 drift tickets open, 0 new, 0 closed", STAFF.eko.email),
      j(4518, "tenant energi", "failed", 5 * 60, 31, "Semaphore: project create failed: HTTP 502 Bad Gateway", STAFF.dimas.email),
      j(4517, "devices energi", "done", 5 * 60 - 1, 3, "3 devices, all Planned: nothing to provision", STAFF.dimas.email),
    ],
  };
}

export function buildState(now: number): DemoState {
  const state: DemoState = {
    now,
    me: meFor(STAFF.dimas, now),
    tools: tools(),
    customers: CUSTOMERS.map((c) => ({ ...c, sites: [...c.sites] })),
    devices: devices(now),
    links: links(),
    outages: outages(now),
    problems: problems(now),
    tickets: tickets(now),
    changes: changes(now),
    maintenance: maintenance(now),
    templates: templates(),
    tasks: tasks(now),
    audit: audit(now),
    sessions: sessions(now),
    glue: glue(now),
    ids: { ticket: 1049, change: 63, task: 321, audit: 401, maintenance: 92, job: 4524, event: 40830 },
  };
  // Devices that are unreachable right now, and the one inside a maintenance window.
  for (const dev of state.devices) {
    if (!dev.host) continue;
    if (state.outages.some((o) => o.device === dev.name && o.minutes === null)) {
      dev.host.state = "down";
      dev.host.error = `Timeout while connecting to ${dev.ip}:161`;
    }
    if (dev.name === "jks01-sw02") dev.host.maintenance = true;
  }
  refreshLinks(state);
  return state;
}

/** A cable is down when one end is unreachable, unknown when both are (or a device is still planned). */
export function refreshLinks(state: DemoState): void {
  const byName = new Map(state.devices.map((x) => [x.name, x]));
  for (const link of state.links) {
    const a = byName.get(link.a);
    const b = byName.get(link.b);
    const aDown = a?.host?.state === "down";
    const bDown = b?.host?.state === "down";
    if ([a, b].some((x) => x && x.status === "Planned")) link.state = "unknown";
    else if (aDown && bDown) link.state = "unknown";
    else if (aDown || bDown) link.state = "down";
    else if (link.a === "mdn01-rtr01" && link.aInterface === "Gi0/0/0") link.state = "down";
    else link.state = "up";
  }
}
