/* Sample path tests, capacity forecasts and monthly reports for the preview (kit 1.6.0). The same shapes and
   the same verdict rules as the console (glue/msp_glue/console/observe.py and reports.py), from made-up values. */
import type {
  CapacityLink,
  PathCollector,
  PathSegment,
  PathState,
  PathTarget,
  PathTargetSetting,
  PathVerdict,
  PathsView,
  ReportFacts,
  ReportFlag,
  ReportLanguage,
  ReportNarrative,
} from "../types";
import { DAY, hash, HOUR, iso, MIN, rand } from "./clock";
import type { DemoCustomer } from "./sample";

// ------------------------------------------------------------------------------------ path tests
interface TargetSpec {
  name: string;
  segment: PathSegment;
  address: string;
  url?: string;
  checks: PathTarget["checks"];
  /** typical value in milliseconds and how it behaves now */
  base: number;
  now?: "slow" | "lossy" | "down";
  only?: string;
}

const DEFAULTS: TargetSpec[] = [
  { name: "Internet reference (Cloudflare)", segment: "internet", address: "1.1.1.1", checks: ["icmp"], base: 9 },
  { name: "Internet reference (Google)", segment: "internet", address: "8.8.8.8", checks: ["icmp"], base: 11 },
  { name: "Microsoft 365", segment: "application", address: "outlook.office365.com", url: "https://outlook.office365.com/", checks: ["https"], base: 240 },
  { name: "Google services", segment: "application", address: "www.google.com", url: "https://www.google.com/generate_204", checks: ["https"], base: 85 },
];

const COLLECTORS: Record<string, { name: string; own: TargetSpec[]; trouble?: Record<string, TargetSpec["now"]> }[]> = {
  bankx: [
    {
      name: "bankx-ckr-01",
      own: [
        { name: "IOH edge router Cikarang", segment: "ioh", address: "10.71.255.1", checks: ["icmp"], base: 2 },
        { name: "Core banking (DR Batam)", segment: "application", address: "10.71.8.40", url: "https://corebank.bankcontoh.example/health", checks: ["https"], base: 120 },
      ],
    },
    {
      name: "bankx-mdn-01",
      own: [{ name: "IOH edge router Medan", segment: "ioh", address: "10.71.255.9", checks: ["icmp"], base: 4 }],
      trouble: { "IOH edge router Medan": "lossy", "Internet reference (Cloudflare)": "lossy", "Internet reference (Google)": "lossy", "Microsoft 365": "slow" },
    },
  ],
  acme: [
    {
      name: "acme-jkt-01",
      own: [
        { name: "IOH edge router Jakarta", segment: "ioh", address: "10.23.255.1", checks: ["icmp"], base: 3 },
        { name: "POS cloud", segment: "application", address: "pos.acme.example", url: "https://pos.acme.example/status", checks: ["https", "icmp"], base: 180 },
      ],
      trouble: { "Microsoft 365": "slow" },
    },
  ],
};

const ORDER: Record<string, number> = { bad: 0, warn: 1, unknown: 2, info: 3, ok: 4 };

function segmentStates(targets: PathTarget[]): PathCollector["segments"] {
  const out: PathCollector["segments"] = {};
  for (const segment of ["ioh", "internet", "application"] as PathSegment[]) {
    const mine = targets.filter((t) => t.segment === segment).map((t) => t.state);
    if (!mine.length) continue;
    const troubled = mine.filter((s) => s === "bad" || s === "warn");
    out[segment] = segment === "internet" && troubled.length && troubled.length < mine.length
      ? "partial"
      : mine.reduce((a, b) => (ORDER[a] <= ORDER[b] ? a : b));
  }
  return out;
}

export function verdict(targets: PathTarget[], online: boolean): PathVerdict {
  const states = segmentStates(targets);
  const trouble = (s: PathSegment) => states[s] === "bad" || states[s] === "warn";
  if (!online) return { side: "collector", tone: "warn", text: "The site collector is not sending data, so the path cannot be judged. Check the collector and its outbound connection on port 10051." };
  if (states.ioh && trouble("ioh")) return { side: "ioh", tone: "bad", text: "Between the site and IOH's edge router: IOH's access link, or the customer's own network at the site." };
  if (trouble("internet")) {
    return states.ioh
      ? { side: "internet", tone: "bad", text: "Beyond IOH's edge router, on the way to the internet. The link to the site itself is fine." }
      : { side: "internet", tone: "bad", text: "Between the site and the internet. Add IOH's edge router at the site as a target to tell IOH's link apart from the rest of the way." };
  }
  const apps = targets.filter((t) => t.segment === "application" && (t.state === "bad" || t.state === "warn")).map((t) => t.name);
  if (apps.length) return { side: "application", tone: "warn", text: `At the application provider (${apps.join(", ")}): the path to the internet is fine.` };
  return { side: "none", tone: "ok", text: "Every part of the path is fine." + (states.ioh ? "" : " Add IOH's edge router at the site as a target to watch IOH's link on its own.") };
}

function target(spec: TargetSpec, collector: string, trouble: TargetSpec["now"], now: number): PathTarget {
  const seed = hash(`${collector}:${spec.name}`);
  const web = spec.checks.includes("https");
  const limit = web ? 2000 : 150;
  const factor = trouble === "slow" ? 9 : trouble === "lossy" ? 3.5 : 1;
  const history: [number, number][] = [];
  const start = Math.floor(now / HOUR) * HOUR - 23 * HOUR;
  for (let i = 0; i < 24; i++) {
    const late = i >= 20 && trouble ? factor : 1;
    history.push([start + i * HOUR + 30 * MIN, +(spec.base * late * (0.85 + 0.3 * rand(seed, i))).toFixed(1)]);
  }
  const last = history[history.length - 1][1];
  const problems = [];
  if (trouble === "lossy") problems.push({ name: `Packet loss to ${spec.name} from ${collector} above 5%`, severity: 2 as const, since: iso(now - 52 * MIN) });
  if (trouble === "slow") {
    problems.push(web
      ? { name: `Slow web response from ${spec.name} at ${collector}`, severity: 2 as const, since: iso(now - 38 * MIN) }
      : { name: `Delay to ${spec.name} from ${collector} above 0.15s`, severity: 2 as const, since: iso(now - 38 * MIN) });
  }
  const state: PathState = problems.length ? "warn" : "ok";
  return {
    name: spec.name, segment: spec.segment, checks: spec.checks, address: spec.address, url: spec.url ?? "",
    delayMs: spec.checks.includes("icmp") ? (web ? +(spec.base / 6).toFixed(1) : last) : null,
    lossPct: spec.checks.includes("icmp") ? (trouble === "lossy" ? +(6 + 6 * rand(seed, 99)).toFixed(1) : 0) : null,
    connectMs: spec.checks.includes("tcp") ? last : null,
    webMs: web ? last : null, webStatus: web ? (spec.url?.endsWith("generate_204") ? 204 : 200) : null, webError: "",
    limits: { delayMs: 150, lossPct: 5, webMs: limit }, broken: [], problems, state,
    historyKind: spec.checks.includes("icmp") && !web ? "delay" : web ? "web" : "connect", history,
    lastSeen: iso(now - 40_000),
  };
}

export function demoPaths(c: DemoCustomer, now: number): PathsView {
  const list = COLLECTORS[c.code] ?? [];
  const collectors: PathCollector[] = list.map((col) => {
    const specs = [...col.own.filter((t) => t.segment === "ioh"), ...DEFAULTS, ...col.own.filter((t) => t.segment !== "ioh")];
    const targets = specs.map((s) => target(s, col.name, col.trouble?.[s.name], now));
    return { name: col.name, online: true, lastSeen: iso(now - 25_000), targets, segments: segmentStates(targets), verdict: verdict(targets, true) };
  });
  const settings: PathTargetSetting[] = [...DEFAULTS, ...list.flatMap((col) => col.own.map((t) => ({ ...t, only: col.name })))].map((t) => ({
    name: t.name, segment: t.segment, address: t.address, url: t.url ?? "", port: 443, checks: t.checks,
    rtt_ms: 150, loss_percent: 5, http_ms: 2000, collector: t.only ?? "",
  }));
  return { collectors, hasCollector: list.length > 0, targets: settings, errors: [], freshness: iso(now - 20_000) };
}

// -------------------------------------------------------------------------------------- capacity
interface LinkSpec {
  customer: string;
  device: string;
  site: string;
  iface: string;
  circuit: string;
  mbps: number;
  /** busy hour four weeks ago and now, in Mbit/s */
  from: number;
  to: number;
  ticket?: number;
}

const LINKS: LinkSpec[] = [
  { customer: "acme", device: "jkt01-rtr01", site: "ACME HQ Jakarta", iface: "GigabitEthernet0/0/0", circuit: "IOH-ACM-0114", mbps: 100, from: 68, to: 86, ticket: 4521 },
  { customer: "bankx", device: "mdn01-rtr01", site: "Branch Medan", iface: "GigabitEthernet0/0/0", circuit: "IOH-BKX-0231", mbps: 50, from: 27, to: 37, ticket: 4530 },
  { customer: "bankx", device: "ckr01-rtr01", site: "Bank Contoh DC Cikarang", iface: "TenGigabitEthernet0/1/0", circuit: "IOH-BKX-0007", mbps: 1000, from: 480, to: 520 },
  { customer: "logix", device: "ckr03-rtr01", site: "Hub Cikarang", iface: "GigabitEthernet0/0/1", circuit: "IOH-LGX-0045", mbps: 200, from: 96, to: 131, ticket: 4533 },
  { customer: "medika", device: "jks01-rtr01", site: "RS Contoh Medika Jakarta Selatan", iface: "GigabitEthernet0/0/0", circuit: "IOH-MDK-0019", mbps: 300, from: 150, to: 152 },
  { customer: "pabrik", device: "krw01-rtr01", site: "Plant Karawang", iface: "GigabitEthernet0/0/0", circuit: "IOH-PBR-0003", mbps: 50, from: 21, to: 22 },
];

export function demoCapacity(names: Record<string, string>, now: number, warn = 80, horizon = 45): CapacityLink[] {
  return LINKS.filter((l) => names[l.customer]).map((l) => {
    const seed = hash(l.device + l.iface);
    const days = l.customer === "pabrik" ? 9 : 28;
    const history: [number, number][] = [];
    for (let i = days; i >= 1; i--) {
      const progress = (days - i) / 27;
      const weekend = new Date(now - i * DAY).getUTCDay() % 6 === 0 ? 0.82 : 1;
      history.push([now - i * DAY, Math.round((l.from + (l.to - l.from) * progress) * weekend * (0.94 + 0.12 * rand(seed, i)) * 1e6)]);
    }
    const recent = history.slice(-7).reduce((s, h) => s + h[1], 0) / Math.min(7, history.length);
    const capacity = l.mbps * 1e6;
    const slope = days >= 14 ? ((l.to - l.from) / 27) * 1e6 : null;
    const utilisation = +((100 * recent) / capacity).toFixed(1);
    let status: CapacityLink["status"] = "ok";
    let daysToWarn: number | null = null;
    if (days < 14) status = "too_little_data";
    else if (utilisation >= warn) { status = "over"; daysToWarn = 0; }
    else if (slope && slope > 0) {
      daysToWarn = +(((warn / 100) * capacity - recent) / slope).toFixed(1);
      status = daysToWarn <= horizon ? "at_risk" : "ok";
      if (daysToWarn > 730) daysToWarn = null;
    }
    return {
      customer: l.customer, customerName: names[l.customer], device: l.device, site: l.site, interface: l.iface, description: "WAN to IOH",
      circuit: l.circuit, provider: "IOH", capacityBps: capacity, capacityFrom: "circuit", direction: "in", status,
      busyBps: Math.round(recent), peakBps: Math.max(...history.map((h) => h[1])), utilisation, slopeBpsPerDay: slope ? Math.round(slope) : null,
      daysToWarn, days, history, ticketId: l.ticket && status !== "ok" ? l.ticket : null,
      ticketLink: l.ticket && status !== "ok" ? `https://glpi.msp.example/front/ticket.form.php?id=${l.ticket}` : null,
    };
  });
}

// --------------------------------------------------------------------------------------- reports
const MONTHS: Record<ReportLanguage, string[]> = {
  id: ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"],
  en: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
};

export function reportMonthLabel(month: string, language: ReportLanguage = "en"): string {
  const [y, m] = month.split("-");
  return `${MONTHS[language][Number(m) - 1]} ${y}`;
}

const num = (v: number | null | undefined, lang: ReportLanguage, digits = 2) => {
  if (v === null || v === undefined) return "-";
  const text = new Intl.NumberFormat(lang === "id" ? "id-ID" : "en-GB", { maximumFractionDigits: digits }).format(v);
  return text;
};

export function demoFacts(c: DemoCustomer, month: string, language: ReportLanguage, previous: number | null, links: CapacityLink[],
  paths: PathsView): ReportFacts {
  const slo = { gold: 99.9, silver: 99.5, bronze: 99.0 }[c.tier];
  const achieved = previous;
  const minutes = achieved === null ? null : +(((100 - achieved) / 100) * 31 * 24 * 60).toFixed(1);
  const opened = Math.round(c.month.opened * 1.4);
  const pathRows = paths.collectors.flatMap((col) => col.targets);
  const nearOrOver = links.filter((l) => l.status === "over" || l.status === "at_risk").length;
  return {
    path_overview: { targets: pathRows.length, with_problems: pathRows.filter((t) => t.problems.length).length },
    capacity_overview: { links: links.filter((l) => ["over", "at_risk", "ok"].includes(l.status)).length, warning_percent: 80, near_or_over_warning: nearOrOver },
    period: { month, label: reportMonthLabel(month, language) },
    availability: { target_percent: slo, achieved_percent: achieved, previous_month_percent: c.olderMonths[3] ?? null, downtime_minutes: minutes,
      met: achieved === null ? undefined : achieved >= slo },
    tickets: { opened, solved: opened - 1, service_level_breaches: c.month.ownLate + c.month.resolveLate, taken_late: c.month.ownLate,
      resolved_late: c.month.resolveLate, mean_hours_to_resolve: c.month.mttrHours },
    configuration: { devices_checked: 8, compliant_percent: 88, backups_fresh_percent: 100 },
    problems: { count: 3, total_minutes: Math.round((minutes ?? 0) * 1.6), longest: minutes ? [
      { device: `${c.sites[0].id}-rtr01`, problem: "Unavailable by ICMP ping", severity: "High", started: `${month}-14T09:12`, minutes: Math.round(minutes * 0.8) },
    ] : [] },
    paths: paths.collectors.flatMap((col) => col.targets.map((t) => ({
      collector: col.name, target: t.name, segment: t.segment, problems: t.problems.length ? 2 : 0,
      ...(t.delayMs !== null ? { delay_ms: t.delayMs } : {}), ...(t.webMs !== null ? { web_ms: Math.round(t.webMs * 0.6) } : {}),
      ...(t.lossPct !== null ? { loss_percent: t.lossPct ? 0.4 : 0 } : {}),
    }))),
    capacity: links.filter((l) => l.status === "over" || l.status === "at_risk" || l.status === "ok").map((l) => ({
      device: l.device, interface: l.interface, circuit: l.circuit ?? "", capacity_mbps: (l.capacityBps ?? 0) / 1e6,
      busy_hour_mbps: +((l.busyBps ?? 0) / 1e6).toFixed(1), utilisation_percent: l.utilisation, days_to_warning: l.daysToWarn, status: l.status,
    })),
  };
}

export function demoNarrative(f: ReportFacts, lang: ReportLanguage): ReportNarrative {
  const a = f.availability;
  const t = f.tickets;
  const risky = Array.isArray(f.capacity) ? f.capacity.filter((x) => x.status !== "ok") : [];
  const paths = Array.isArray(f.paths) ? f.paths : [];
  const troubled = { length: f.path_overview?.with_problems ?? paths.filter((p) => p.problems).length };
  const n = (v: number | null | undefined, d = 2) => num(v, lang, d);
  if (lang === "id") {
    return {
      summary: `Ketersediaan layanan pada ${f.period.label} mencapai ${n(a.achieved_percent, 3)}% dari target ${n(a.target_percent)}%. `
        + `Sebanyak ${t.opened} tiket dibuka dan ${t.solved} diselesaikan.` + (risky.length ? ` ${risky.length} jalur mendekati batas kapasitas pada jam tersibuk.` : ""),
      availability: `Total gangguan tercatat ${n(a.downtime_minutes, 1)} menit${a.met === false ? ", sehingga ketersediaan berada di bawah target yang disepakati" : ""}.`,
      service: `Rata-rata waktu penyelesaian tiket ${n(t.mean_hours_to_resolve, 1)} jam; ${t.service_level_breaches ? `${t.service_level_breaches} tiket melewati batas waktu layanan` : "seluruh tiket ditangani dalam batas waktu layanan"}.`,
      paths: paths.length ? `Uji jalur dari lokasi pelanggan memeriksa ${paths.length} tujuan; ${troubled.length ? `${troubled.length} di antaranya sempat mencatat masalah` : "tidak ada masalah yang tercatat"}.` : "",
      capacity: risky.length ? `Jalur ${risky[0].interface} pada ${risky[0].device} terpakai ${n(risky[0].utilisation_percent, 1)}% pada jam tersibuk.` : "",
      recommendations: risky.slice(0, 2).map((r) => `Kami menyarankan peninjauan kapasitas jalur ${r.interface} pada ${r.device}.`),
    };
  }
  return {
    summary: `Availability in ${f.period.label} was ${n(a.achieved_percent, 3)}% against a target of ${n(a.target_percent)}%. `
      + `${t.opened} tickets were opened and ${t.solved} solved.` + (risky.length ? ` ${risky.length} links are close to their capacity at the busy hour.` : ""),
    availability: `Downtime totalled ${n(a.downtime_minutes, 1)} minutes${a.met === false ? ", below the agreed target" : ""}.`,
    service: `Tickets took ${n(t.mean_hours_to_resolve, 1)} hours to resolve on average; ${t.service_level_breaches ? `${t.service_level_breaches} missed their service level` : "every ticket was handled within its service level"}.`,
    paths: paths.length ? `Path tests from the customer's sites checked ${paths.length} targets; ${troubled.length ? `${troubled.length} of them recorded problems` : "none recorded a problem"}.` : "",
    capacity: risky.length ? `Link ${risky[0].interface} on ${risky[0].device} ran at ${n(risky[0].utilisation_percent, 1)}% at its busy hour.` : "",
    recommendations: risky.slice(0, 2).map((r) => `We recommend a capacity review of ${r.interface} on ${r.device}.`),
  };
}

/** The numbers in the text that the facts do not contain (a short version of the console's check). */
export function demoFlags(text: ReportNarrative, f: ReportFacts): ReportFlag[] {
  const known = new Set<string>();
  const walk = (v: unknown) => {
    if (typeof v === "number") for (let d = 0; d <= 3; d++) known.add(String(Number(v.toFixed(d))));
    else if (Array.isArray(v)) { known.add(String(v.length)); v.forEach(walk); }
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
    else if (typeof v === "string") (v.match(/\b\d+\b/g) ?? []).forEach((x) => known.add(String(Number(x))));
  };
  walk(f);
  known.add("80");
  const flags: ReportFlag[] = [];
  for (const section of ["summary", "availability", "service", "paths", "capacity", "recommendations"] as const) {
    const parts = section === "recommendations" ? text.recommendations : [text[section]];
    for (const part of parts) {
      for (const token of part.match(/(?<![\w.,])\d+(?:[.,]\d+)*(?![\w])/g) ?? []) {
        const plain = token.includes(",") && !token.includes(".") ? token.replace(",", ".") : token.replace(/\.(?=\d{3}\b)/g, "").replace(",", ".");
        if (!known.has(String(Number(plain))) && !known.has(String(Number(token.replace(/[.,]/g, ""))))) flags.push({ section, number: token });
      }
    }
  }
  return flags;
}
