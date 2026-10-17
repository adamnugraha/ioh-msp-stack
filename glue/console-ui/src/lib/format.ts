/* Dates, durations and numbers in the platform's time zone and the console's wording. */

let zone = "Asia/Jakarta";
const ZONE_LABELS: Record<string, string> = { "Asia/Jakarta": "WIB", "Asia/Makassar": "WITA", "Asia/Jayapura": "WIT", UTC: "UTC" };

export function setTimeZone(tz: string) {
  if (tz && tz !== zone) {
    zone = tz;
    cache.clear();
  }
}
export const zoneLabel = () => ZONE_LABELS[zone] ?? zone;
export const timeZone = () => zone;

const cache = new Map<string, Intl.DateTimeFormat>();
function formatter(key: string, options: Intl.DateTimeFormatOptions) {
  let f = cache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-GB", { ...options, timeZone: zone });
    cache.set(key, f);
  }
  return f;
}

const ms = (v: string | number | Date) => (typeof v === "number" ? v : typeof v === "string" ? Date.parse(v) : v.getTime());

function dayOf(t: number) {
  return formatter("day-key", { year: "numeric", month: "2-digit", day: "2-digit" }).format(t);
}

/** "14:05" today, "16 Oct 14:05" otherwise. */
export function when(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const t = ms(value);
  if (dayOf(t) === dayOf(Date.now())) return formatter("hm", { hour: "2-digit", minute: "2-digit" }).format(t);
  return formatter("dmhm", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(t);
}

/** "17 Oct 2026, 14:05:12 WIB" */
export function whenFull(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  return `${formatter("full", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(ms(value))} ${zoneLabel()}`;
}

/** "Fri 17 Oct" */
export function dayLabel(value: string | number): string {
  return formatter("wdm", { weekday: "short", day: "numeric", month: "short" }).format(ms(value));
}

export function timeOnly(value: string | number): string {
  return formatter("hm", { hour: "2-digit", minute: "2-digit" }).format(ms(value));
}

export function isToday(value: string | number): boolean {
  return dayOf(ms(value)) === dayOf(Date.now());
}

export function sameDay(a: string | number, b: string | number): boolean {
  return dayOf(ms(a)) === dayOf(ms(b));
}

/** Local date and time for an <input type="datetime-local">, in the platform's time zone. */
export function toLocalInput(value: number): string {
  const parts = formatter("input", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(value)
    .reduce<Record<string, string>>((acc, p) => ({ ...acc, [p.type]: p.value }), {});
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

/** The instant an <input type="datetime-local"> value means in the platform's time zone. */
export function fromLocalInput(value: string): number {
  const [date, time] = value.split("T");
  const [y, mo, d] = date.split("-").map(Number);
  const [h, mi] = (time ?? "00:00").split(":").map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  // the zone's offset at that moment: format the guess and compare
  const shown = toLocalInput(guess);
  const [sd, st] = shown.split("T");
  const [sy, smo, sdd] = sd.split("-").map(Number);
  const [sh, smi] = st.split(":").map(Number);
  const offset = Date.UTC(sy, smo - 1, sdd, sh, smi) - guess;
  return guess - offset;
}

/** "3 min", "2 h 10 min", "3 days 4 h" */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))} min`;
  if (s < 172800) {
    const m = Math.floor((s % 3600) / 60);
    return m ? `${Math.floor(s / 3600)} h ${m} min` : `${Math.floor(s / 3600)} h`;
  }
  const h = Math.floor((s % 86400) / 3600);
  return h ? `${Math.floor(s / 86400)} days ${h} h` : `${Math.floor(s / 86400)} days`;
}

/** Time since a moment, as a duration: "23 min". */
export function since(value: string | number, now = Date.now()): string {
  return duration((now - ms(value)) / 1000);
}

/** "just now", "5 min ago", "3 h ago", "in 2 h" */
export function ago(value: string | number | null | undefined, now = Date.now()): string {
  if (value === null || value === undefined || value === "") return "never";
  const seconds = Math.round((now - ms(value)) / 1000);
  if (seconds < 0) {
    const ahead = -seconds;
    if (ahead < 90) return "in a minute";
    if (ahead < 5400) return `in ${Math.round(ahead / 60)} min`;
    if (ahead < 172800) return `in ${Math.round(ahead / 3600)} h`;
    return `in ${Math.round(ahead / 86400)} days`;
  }
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

export function pct(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "" : `${value.toFixed(digits)} %`;
}

export function hours(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (value < 1) return `${Math.max(1, Math.round(value * 60))} min`;
  return value < 48 ? `${value.toFixed(1)} h` : `${(value / 24).toFixed(1)} days`;
}

/** Bits per second: "940 kbit/s", "182 Mbit/s", "1.2 Gbit/s" */
export function bits(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (value >= 1e9) return `${(value / 1e9).toFixed(value >= 1e10 ? 0 : 1)} Gbit/s`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(value >= 1e8 ? 0 : 1)} Mbit/s`;
  if (value >= 1e3) return `${Math.round(value / 1e3)} kbit/s`;
  return `${Math.round(value)} bit/s`;
}

export function speed(value: number): string {
  if (value >= 1e9) return `${value / 1e9} Gbit/s`;
  return `${Math.round(value / 1e6)} Mbit/s`;
}

/** Axis ticks: 0, 50M, 1.5G */
export function compactBits(value: number): string {
  if (value >= 1e9) return `${+(value / 1e9).toFixed(1)}G`;
  if (value >= 1e6) return `${+(value / 1e6).toFixed(0)}M`;
  if (value >= 1e3) return `${+(value / 1e3).toFixed(0)}k`;
  return String(Math.round(value));
}

export function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** Downtime in seconds as "1 h 13 min", or "none". */
export function downtime(seconds: number | null | undefined): string {
  if (!seconds) return "none";
  return duration(seconds);
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}
