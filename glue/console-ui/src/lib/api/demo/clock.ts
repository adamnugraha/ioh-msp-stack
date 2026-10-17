/* Time helpers for the sample data. Everything is placed relative to the moment the page opened,
   so ages ("23 min") look the way they would on a live platform. Local time is WIB (UTC+7). */

export const MIN = 60_000;
export const HOUR = 3_600_000;
export const DAY = 86_400_000;
const WIB = 7 * HOUR;

export const iso = (ms: number) => new Date(ms).toISOString();

/** Midnight WIB of the day that contains ms. */
export function wibMidnight(ms: number): number {
  return Math.floor((ms + WIB) / DAY) * DAY - WIB;
}

/** A local (WIB) clock time, dayOffset days from today. */
export function at(now: number, dayOffset: number, hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return wibMidnight(now) + dayOffset * DAY + h * HOUR + m * MIN;
}

/** The next full quarter hour at least `minutes` from now. */
export function nextQuarter(now: number, minutes: number): number {
  const t = now + minutes * MIN;
  return Math.ceil(t / (15 * MIN)) * 15 * MIN;
}

export function localHour(ms: number): number {
  return (((ms + WIB) % DAY) + DAY) % DAY / HOUR;
}

export function monthStart(ms: number, offset = 0): number {
  const d = new Date(ms + WIB);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + offset, 1) - WIB;
}

export function monthKey(ms: number): string {
  const d = new Date(ms + WIB);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function monthLabel(ms: number): string {
  return new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "Asia/Jakarta" }).format(ms);
}

export function dayKey(ms: number): string {
  const d = new Date(ms + WIB);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

/* Small deterministic random numbers, so the sample charts are the same on every load. */
export function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function rand(seed: number, n: number): number {
  let t = (seed ^ Math.imul(n, 0x9e3779b1)) >>> 0;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
