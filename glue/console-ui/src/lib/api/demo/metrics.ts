/* Sample Zabbix values: deterministic per device, metric and minute, so a chart shows the same line
   every time and the ranges agree with each other. The stories in the sample (a store that went dark,
   a busy firewall, a filling disk) show up in the values too. */
import type { ChartInfo, Device, DeviceMetrics, Interface, MetricChart, Series, TimeRange } from "../types";
import { DAY, hash, HOUR, localHour, MIN, rand } from "./clock";
import { CUSTOMERS, type DemoCustomer, type DemoState, SPARE_PORTS } from "./sample";

const RANGES: Record<TimeRange, { span: number; step: number; source: "history" | "trends" }> = {
  "1h": { span: HOUR, step: MIN, source: "history" },
  "6h": { span: 6 * HOUR, step: MIN, source: "history" },
  "24h": { span: DAY, step: 2 * MIN, source: "history" },
  "7d": { span: 7 * DAY, step: HOUR, source: "trends" },
  "30d": { span: 30 * DAY, step: HOUR, source: "trends" },
};

const WIB = 7 * HOUR;
const bump = (h: number, centre: number, width: number) => Math.exp(-((h - centre) ** 2) / (2 * width * width));

/** How busy a customer's network is at a moment, 0.05 to 1. */
function load(profile: DemoCustomer["traffic"]["profile"], t: number): number {
  const h = localHour(t);
  const weekday = new Date(t + WIB).getUTCDay();
  const weekend = weekday === 0 || weekday === 6;
  let v: number;
  switch (profile) {
    case "office":
      v = 0.08 + 0.92 * Math.min(1, bump(h, 10.5, 2.2) + bump(h, 15, 2.2)) * (weekend ? 0.2 : 1);
      break;
    case "retail":
      v = 0.06 + 0.94 * Math.min(1, 0.55 * bump(h, 13, 1.6) + 0.75 * bump(h, 19, 1.8) + 0.4 * bump(h, 16, 3)) * (weekend ? 1.15 : 1);
      break;
    case "hospital":
      v = 0.4 + 0.6 * Math.min(1, bump(h, 10, 3) + 0.6 * bump(h, 16, 3));
      break;
    case "plant":
      v = 0.62 - 0.25 * (bump(h, 6, 0.4) + bump(h, 14, 0.4) + bump(h, 22, 0.4));
      break;
    case "logistics":
      v = 0.12 + 0.88 * Math.min(1, bump(h, 8, 2.5) + 0.8 * bump(h, 20, 2.5)) * (weekday === 0 ? 0.5 : 1);
      break;
  }
  return Math.max(0.05, Math.min(1, v));
}

const REGION_RTT: Record<string, number> = {
  Jakarta: 5, "West Java": 7, "East Java": 14, "North Sumatra": 27, "South Sulawesi": 31, Bali: 21, "Riau Islands": 17,
};

interface Ctx {
  device: Device;
  customer: DemoCustomer;
  down: [number, number][];
  seed: number;
  /** the moment the sample is anchored to (when the page opened); the stories are timed from it */
  now: number;
  /** the latest value: the real current time */
  end: number;
  region: string;
}

function ctx(state: DemoState, device: Device): Ctx {
  const customer = state.customers.find((c) => c.code === device.customer) ?? CUSTOMERS[0];
  const down = state.outages
    .filter((o) => o.device === device.name)
    .map((o): [number, number] => [o.start, o.minutes === null ? Infinity : o.start + o.minutes * MIN]);
  const region = customer.sites.find((s) => device.name.startsWith(s.id))?.region ?? "Jakarta";
  return { device, customer, down, seed: hash(device.name), now: state.now, end: Date.now(), region };
}

const isDown = (c: Ctx, t: number) => c.down.some(([a, b]) => t >= a && t < b);
const noise = (c: Ctx, key: string, t: number) => rand(c.seed ^ hash(key), Math.floor(t / MIN));

function value(c: Ctx, chart: string, t: number): number | null {
  if (t > c.end) return null;
  const dn = isDown(c, t);
  const n = noise(c, chart, t);
  const l = load(c.customer.traffic.profile, t);
  const name = c.device.name;
  switch (chart) {
    case "icmp_loss": {
      if (dn) return 100;
      return n > 0.995 ? Math.round(n * 1000 - 994) : 0;
    }
    case "icmp_rtt": {
      if (dn) return null;
      if (name === "jkt01-rtr01" && t > c.now - 185 * MIN) return 118 + 72 * n + 25 * Math.sin(t / (7 * MIN));
      const base = REGION_RTT[c.region] ?? 8;
      return +(base + 2.5 * n + 14 * l ** 4 + (n > 0.985 ? 30 * n : 0)).toFixed(1);
    }
    case "cpu": {
      if (dn) return null;
      if (name === "ckr01-fw01" && t > c.now - 80 * MIN) {
        const ramp = Math.min(1, (t - (c.now - 80 * MIN)) / (12 * MIN));
        return +(45 + ramp * 46 + 3 * n).toFixed(1);
      }
      const base = { router: 9, firewall: 28, switch: 6, server: 12, wireless: 8 }[c.device.role];
      return +(base + 22 * l + 6 * n).toFixed(1);
    }
    case "memory": {
      if (dn) return null;
      if (name === "ckr03-sw01") {
        const leakStart = c.now - 8 * DAY;
        const plateau = c.now - 27 * HOUR;
        if (t < leakStart) return +(66 + 2 * n).toFixed(1);
        if (t < plateau) return +(66 + (26 * (t - leakStart)) / (plateau - leakStart) + 1.5 * n).toFixed(1);
        return +(91 + 1.6 * n).toFixed(1);
      }
      const base = { router: 38, firewall: 54, switch: 31, server: 58, wireless: 30 }[c.device.role];
      return +(base + 4 * l + 2 * n).toFixed(1);
    }
    case "fs:/var/lib/pacs": {
      if (dn) return null;
      const t0 = c.now - 30 * DAY;
      const fast = c.now - 24 * HOUR;
      if (t < fast) return +(62 + ((t - t0) / DAY) * 0.62 + 0.3 * n).toFixed(1);
      return +(80.1 + ((t - fast) / DAY) * 16 + 0.2 * n).toFixed(1);
    }
    case "fs:/": {
      if (dn) return null;
      return +(41 + ((t - (c.now - 30 * DAY)) / DAY) * 0.03 + 0.2 * n).toFixed(1);
    }
  }
  return null;
}

/* Interfaces and their traffic ------------------------------------------------------------- */
interface Port {
  name: string;
  description: string;
  peer: Interface["peer"];
  circuit: string | null;
  speed: number;
  state: "up" | "down" | "unknown";
  wan: boolean;
}

function ports(state: DemoState, device: Device): Port[] {
  const out: Port[] = [];
  for (const link of state.links) {
    const mine = link.a === device.name ? "a" : link.b === device.name ? "b" : null;
    if (!mine) continue;
    const name = mine === "a" ? link.aInterface : link.bInterface;
    const peerName = mine === "a" ? link.b : link.a;
    const peerIf = mine === "a" ? link.bInterface : link.aInterface;
    const wan = link.kind === "circuit";
    out.push({
      name,
      description: wan ? `WAN: ${link.circuit}` : `To ${peerName} ${peerIf}`,
      peer: wan ? { device: "IOH network", interface: link.circuit ?? "" } : { device: peerName, interface: peerIf },
      circuit: link.circuit,
      speed: link.speed,
      state: link.state,
      wan,
    });
  }
  return out;
}

const SITE_SHARE: Record<string, number> = {
  ckr01: 1, btm01: 0.35, mdn01: 0.08, sby01: 0.1, mks01: 0.07, dps01: 0.05,
  jkt01: 1, bdg01: 0.16, bks01: 0.14, ckr02: 0.25,
  jks01: 1, dpk01: 0.18,
  ckr03: 1, sby02: 0.55, tpk01: 0.2,
  krw01: 1, jkt02: 1,
};

function traffic(c: Ctx, port: Port, direction: "in" | "out", t: number): number | null {
  if (t > c.end || isDown(c, t)) return null;
  const name = c.device.name;
  const site = name.slice(0, 5);
  const share = SITE_SHARE[site] ?? 0.2;
  const l = load(c.customer.traffic.profile, t);
  const n = noise(c, `${port.name}:${direction}`, t);
  let mbps = c.customer.traffic.peakMbps * share * l * (0.82 + 0.36 * n);
  if (!port.wan) mbps *= c.device.role === "server" ? 0.35 : 0.9;
  if (direction === "out") mbps *= 0.42;
  if (port.name === "ha") mbps = 2 + 0.4 * n;
  // mdn01: the MPLS circuit failed 130 minutes ago and the LTE backup took over
  if (name === "mdn01-rtr01") {
    const failed = c.now - 130 * MIN;
    if (port.name === "Gi0/0/0") return t >= failed ? 0 : mbps * 1e6;
    if (port.name === "Cellular0/2/0") return t >= failed ? Math.min(mbps, 95) * 1e6 : 0.03e6 * (1 + n);
  }
  if (name === "jkt01-rtr01" && port.name === "Gi0/0/0" && t > c.now - 185 * MIN) {
    // the 200 Mbit/s circuit is full inbound from the moment the problem started
    mbps = direction === "in" ? 184 + 13 * n : 58 + 18 * n;
  }
  return Math.round(mbps * 1e6);
}

export function interfacesFor(state: DemoState, device: Device): Interface[] {
  const c = ctx(state, device);
  const list: Interface[] = ports(state, device).map((p) => {
    const deviceDown = device.host?.state === "down";
    const status: Interface["status"] = p.state === "up" ? "up" : "down";
    const errors = device.name === "krw01-sw02" && p.name === "1/0/24" ? 14 : device.name === "krw01-sw01" && p.name === "1/0/26" ? 2 : 0;
    return {
      name: p.name,
      description: p.description,
      status: deviceDown ? "down" : status,
      speed: p.speed,
      inBps: device.host && !deviceDown ? traffic(c, p, "in", c.end - MIN) : null,
      outBps: device.host && !deviceDown ? traffic(c, p, "out", c.end - MIN) : null,
      errorsPerMin: device.host && !deviceDown ? errors : null,
      peer: p.peer,
      circuit: p.circuit,
    };
  });
  for (const spare of SPARE_PORTS[device.role] ?? []) {
    if (list.some((i) => i.name === spare)) continue;
    list.push({ name: spare, description: "", status: "admin-down", speed: 1e9, inBps: null, outBps: null, errorsPerMin: null, peer: null, circuit: null });
  }
  return list;
}

export function chartsFor(state: DemoState, device: Device): ChartInfo[] {
  if (!device.host) return [];
  const list: ChartInfo[] = [
    { id: "icmp_rtt", title: "Response time", units: "ms" },
    { id: "icmp_loss", title: "Packet loss", units: "%" },
    { id: "cpu", title: "CPU", units: "%" },
    { id: "memory", title: "Memory used", units: "%" },
  ];
  if (device.name === "jks01-srv01") list.push({ id: "fs:/var/lib/pacs", title: "Disk used, /var/lib/pacs", units: "%" });
  if (device.role === "server") list.push({ id: "fs:/", title: "Disk used, /", units: "%" });
  for (const p of ports(state, device)) {
    if (p.name === "ha") continue;
    list.push({ id: `if:${p.name}`, title: `Traffic, ${p.name}`, units: "bps", interface: p.name });
  }
  return list;
}

export function metricsFor(state: DemoState, device: Device, range: TimeRange, wanted: string[]): DeviceMetrics {
  const r = RANGES[range];
  const c = ctx(state, device);
  const end = Math.floor(c.end / r.step) * r.step;
  const start = end - r.span;
  const times: number[] = [];
  for (let t = start + r.step; t <= end; t += r.step) times.push(t);
  const available = chartsFor(state, device);
  const portList = ports(state, device);
  const charts: MetricChart[] = [];
  for (const id of wanted) {
    const info = available.find((x) => x.id === id);
    if (!info) continue;
    const at = (f: (t: number) => number | null): [number, number | null][] =>
      times.map((t) => {
        if (r.source === "history") return [t, f(t)];
        // trends: the hourly average of minute values, null where the device answered none of them
        let sum = 0;
        let count = 0;
        for (let m = 0; m < r.step; m += 10 * MIN) {
          const v = f(t - r.step + m + 5 * MIN);
          if (v !== null) {
            sum += v;
            count++;
          }
        }
        return [t, count ? +(sum / count).toFixed(2) : null];
      });
    let series: Series[];
    if (info.units === "bps") {
      const port = portList.find((p) => p.name === info.interface)!;
      series = [
        { key: "in", name: "In", points: at((t) => traffic(c, port, "in", t)) },
        { key: "out", name: "Out", points: at((t) => traffic(c, port, "out", t)) },
      ];
    } else {
      series = [{ key: id, name: info.title, points: at((t) => value(c, id, t)) }];
    }
    charts.push({ ...info, series });
  }
  return { range, source: r.source, step: r.step / 1000, charts };
}
