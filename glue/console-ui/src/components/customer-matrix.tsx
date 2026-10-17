/* Every customer against every tool: one row per customer, one column per tool's view of it. A cell
   is tinted only when something needs attention, and links to the place to deal with it. */
import { CircleCheck, CircleDashed, CirclePause, Info, OctagonX, TriangleAlert, type LucideIcon } from "lucide-react";
import { Link } from "react-router";
import type { CustomerSummary } from "@/lib/api";
import { ago, pct } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { outcomeTone, severityName, severityTone, type Tone } from "@/lib/status";
import { cn } from "@/lib/utils";
import { Pill } from "@/components/status";

interface Cell {
  tone: Tone;
  text: string;
  sub: string;
  to: string;
  icon?: LucideIcon;
}

const ICON: Record<Tone, LucideIcon> = { ok: CircleCheck, warn: TriangleAlert, bad: OctagonX, info: Info, neutral: CircleDashed };

const COLUMNS = [
  { key: "monitoring", label: "Monitoring", tool: "Zabbix" },
  { key: "problems", label: "Problems", tool: "Zabbix" },
  { key: "tickets", label: "Tickets", tool: "GLPI" },
  { key: "changes", label: "Changes", tool: "GLPI, Zabbix" },
  { key: "config", label: "Configuration", tool: "Golden Config" },
  { key: "backups", label: "Backups", tool: "Forgejo" },
  { key: "automation", label: "Automation", tool: "Semaphore" },
  { key: "availability", label: "Availability", tool: "Zabbix SLA" },
] as const;

type Key = (typeof COLUMNS)[number]["key"];

function cellsFor(c: CustomerSummary, now: number): Record<Key, Cell> {
  const base = `/customers/${c.code}`;
  const monitored = c.up + c.down + c.unknown;
  const drift = c.checked - c.compliant;
  const backupAge = c.lastBackup ? now - Date.parse(c.lastBackup) : Infinity;
  const used = c.sla && c.sla.allowed ? c.sla.downtime / c.sla.allowed : 0;
  return {
    monitoring: !c.active
      ? { tone: "neutral", text: "no active devices", sub: `${c.devices} planned`, to: `${base}/devices` }
      : {
          tone: c.down > c.downInMaintenance ? "bad" : c.down ? "info" : c.unknown || c.unmonitored ? "warn" : "ok",
          icon: c.down && c.down === c.downInMaintenance ? CirclePause : undefined,
          text: c.down ? `${c.down} down` : `${c.up} up`,
          sub: c.down && c.down === c.downInMaintenance
            ? "in maintenance"
            : `of ${monitored} monitored${c.downInMaintenance ? `, ${c.downInMaintenance} in maintenance` : ""}${c.unmonitored ? `, ${c.unmonitored} missing` : ""}`,
          to: `${base}/devices`,
        },
    problems: {
      tone: c.problems ? severityTone(c.worst) : "ok",
      text: c.problems ? `${c.problems} open` : "none",
      sub: c.problems ? `worst ${severityName(c.worst!).toLowerCase()}${c.unacknowledged ? `, ${c.unacknowledged} new` : ""}` : "",
      to: `${base}/work`,
    },
    tickets: {
      tone: c.late ? "bad" : "ok",
      text: c.tickets ? `${c.tickets} open` : "none",
      sub: c.late ? `${c.late} past due` : c.tickets ? "on time" : "",
      to: `${base}/work`,
    },
    changes: {
      tone: c.maintenanceNow ? "info" : "neutral",
      icon: c.maintenanceNow ? CirclePause : undefined,
      text: c.changesToday ? `${c.changesToday} today` : "none today",
      sub: c.maintenanceNow ? "maintenance on now" : "",
      to: `${base}/changes`,
    },
    config: !c.inScope
      ? { tone: "neutral", text: "nothing in scope", sub: "", to: `${base}/devices` }
      : {
          tone: !c.checked ? "warn" : drift ? "warn" : "ok",
          text: c.checked ? `${c.compliant} of ${c.checked}` : "not checked",
          sub: c.checked ? (drift ? `${drift} drifted` : "compliant") : "",
          to: `${base}/devices`,
        },
    backups: !c.inScope
      ? { tone: "neutral", text: "nothing in scope", sub: "", to: `${base}/devices` }
      : {
          tone: c.backupFailed ? "bad" : backupAge > 26 * 3600_000 ? "warn" : "ok",
          text: c.lastBackup ? ago(c.lastBackup, now) : "none yet",
          sub: c.backupFailed ? `${c.backupFailed} failed` : "all saved",
          to: `${base}/devices`,
        },
    automation: c.lastRun
      ? { tone: outcomeTone(c.lastRun.status) === "bad" ? "bad" : "ok", text: c.lastRun.status, sub: `${c.lastRun.template}, ${ago(c.lastRun.when, now)}`, to: `${base}/automation` }
      : { tone: "neutral", text: "no runs", sub: "", to: `${base}/automation` },
    availability: c.sla && c.sla.sli !== null
      ? {
          tone: c.sla.sli < c.sla.slo ? "bad" : used > 0.75 ? "warn" : "ok",
          text: pct(c.sla.sli, 2),
          sub: c.sla.sli < c.sla.slo ? `below ${c.sla.slo} %` : `target ${c.sla.slo} %`,
          to: "/service",
        }
      : { tone: "neutral", text: "no agreement yet", sub: "", to: "/service" },
  };
}

const TINT: Record<Tone, string> = {
  ok: "hover:bg-accent",
  neutral: "hover:bg-accent",
  info: "bg-info-bg/70 hover:bg-info-bg",
  warn: "bg-warn-bg/70 hover:bg-warn-bg",
  bad: "bg-bad-bg/80 hover:bg-bad-bg",
};

const ICON_COLOR: Record<Tone, string> = { ok: "text-ok", neutral: "text-muted-foreground", info: "text-info", warn: "text-warn", bad: "text-bad" };

export function CustomerMatrix({ customers }: { customers: CustomerSummary[] }) {
  const now = useNow(60_000);
  return (
    <div className="scroll-x">
      <table className="w-full min-w-[1100px] border-separate border-spacing-0 text-[13px]">
        <thead>
          <tr>
            <th className="sticky left-0 z-10 border-b bg-card px-4 py-2 text-left text-[12.5px] font-medium text-muted-foreground">Customer</th>
            {COLUMNS.map((col) => (
              <th key={col.key} className="border-b px-2 py-2 text-left font-medium">
                <div className="text-[12.5px] text-foreground">{col.label}</div>
                <div className="text-[11.5px] font-normal text-muted-foreground">{col.tool}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {customers.map((c) => {
            const cells = cellsFor(c, now);
            return (
              <tr key={c.code}>
                <th scope="row" className="sticky left-0 z-10 border-b bg-card px-4 py-2 text-left align-top font-normal">
                  <Link to={`/customers/${c.code}`} className="font-semibold hover:underline">
                    {c.name}
                  </Link>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[12px] text-muted-foreground">
                    <span>{c.code}</span>
                    <span className="capitalize">{c.tier}</span>
                    {c.regulated && <span>regulated</span>}
                  </div>
                  {c.provisioning === "error" && (
                    <Pill tone="bad" className="mt-1" title={c.provisioningMessage}>
                      set-up error
                    </Pill>
                  )}
                </th>
                {COLUMNS.map((col) => {
                  const cell = cells[col.key];
                  const Icon = cell.icon ?? ICON[cell.tone];
                  return (
                    <td key={col.key} className="border-b p-1 align-top">
                      <Link to={cell.to} className={cn("block min-h-[46px] rounded-md px-2 py-1.5 transition-colors", TINT[cell.tone])}>
                        <span className="flex items-center gap-1.5 font-medium">
                          <Icon aria-hidden className={cn("size-3.5 shrink-0", ICON_COLOR[cell.tone])} />
                          <span className="num whitespace-nowrap">{cell.text}</span>
                        </span>
                        {cell.sub && <span className="mt-0.5 block text-[12px] leading-snug text-muted-foreground">{cell.sub}</span>}
                      </Link>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
