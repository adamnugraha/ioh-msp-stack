/* A customer at a glance: availability against target, what is open, and what needs someone now. */
import { ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { ago, pct, since } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { severityName } from "@/lib/status";
import { BudgetMeter, DowntimeColumns } from "@/components/charts/service-charts";
import { Empty, Section, Stat } from "@/components/page";
import { OutcomePill, StateDot } from "@/components/status";
import { ToolLink } from "@/components/tool-link";
import { windowText } from "@/components/work-items";
import type { Tone } from "@/lib/status";
import { useCustomerData } from "./layout";

interface Attention {
  tone: Tone;
  text: ReactNode;
  where: string;
  to?: string;
  href?: string | null;
  tool?: "GLPI" | "Zabbix";
}

export function CustomerOverview() {
  const data = useCustomerData();
  const now = useNow(30_000);
  const s = data.summary;
  const monitored = s.up + s.down + s.unknown;

  const attention: Attention[] = [];
  for (const p of data.problems.filter((x) => !x.acknowledged && !x.suppressed)) {
    attention.push({
      tone: p.severity >= 4 ? "bad" : "warn",
      text: <><b className="font-medium">{severityName(p.severity)}:</b> {p.name} on {p.device}, open {since(p.started, now)}, nobody on it yet</>,
      where: "Zabbix",
      to: `/customers/${s.code}/devices/${p.device}`,
    });
  }
  for (const t of data.tickets.filter((x) => x.late)) {
    attention.push({ tone: "bad", text: <>Ticket #{t.id} is past due: {t.name}</>, where: "GLPI", href: t.link, tool: "GLPI" });
  }
  for (const d of data.devices.filter((x) => x.backup?.lastAttempt && x.backup.lastSuccess && x.backup.lastAttempt > x.backup.lastSuccess)) {
    attention.push({
      tone: "bad",
      text: <>Backup of {d.name} failed {ago(d.backup!.lastAttempt, now)}; the last good one is {ago(d.backup!.lastSuccess, now)}</>,
      where: "Golden Config",
      to: `/customers/${s.code}/devices/${d.name}`,
    });
  }
  for (const d of data.devices.filter((x) => x.check?.failed.length)) {
    attention.push({
      tone: "warn",
      text: <>{d.name} differs from its intended configuration ({d.check!.failed.join(", ")}){d.driftTicket ? `, ticket #${d.driftTicket}` : ""}</>,
      where: "Golden Config",
      to: `/customers/${s.code}/devices/${d.name}`,
    });
  }
  for (const d of data.devices.filter((x) => x.status === "Active" && x.monitoring && !x.host && x.ip)) {
    attention.push({ tone: "warn", text: <>{d.name} is active in Nautobot but not in Zabbix yet</>, where: "msp-glue", to: `/customers/${s.code}/devices/${d.name}` });
  }

  const next = [
    ...data.maintenance.filter((m) => m.state !== "ended").map((m) => ({ key: m.id, at: m.start, title: m.name, sub: `Maintenance, ${windowText(m.start, m.end)}`, on: m.state === "active" })),
    ...data.changes
      .filter((c) => c.plannedStart && !["Applied", "Closed", "Review", "Refused", "Cancelled"].includes(c.status) && !data.maintenance.some((m) => m.changeId === c.id))
      .map((c) => ({ key: `c${c.id}`, at: c.plannedStart!, title: `Change #${c.id}: ${c.name}`, sub: `${c.status}, ${windowText(c.plannedStart, c.plannedEnd)}`, on: false })),
  ].sort((a, b) => a.at.localeCompare(b.at));

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat
          label="Availability this month"
          value={s.sla?.sli != null ? pct(s.sla.sli, 3) : "no data"}
          tone={s.sla?.sli != null ? (s.sla.sli < s.sla.slo ? "bad" : "ok") : undefined}
          note={s.sla ? `Target ${s.sla.slo} %` : "No availability agreement in Zabbix yet"}
        >
          {s.sla && <BudgetMeter sla={s.sla} compact className="mt-2.5" />}
        </Stat>
        <Stat
          label="Open problems"
          value={s.problems}
          tone={s.high ? "bad" : s.problems ? "warn" : "ok"}
          note={s.problems ? `${s.high} high or disaster, ${s.unacknowledged} not acknowledged` : "None"}
          to={`/customers/${s.code}/work`}
        />
        <Stat
          label="Devices up"
          value={`${s.up} of ${monitored}`}
          tone={s.down > s.downInMaintenance ? "bad" : "ok"}
          note={`${s.down} down${s.downInMaintenance ? ` (${s.downInMaintenance} in maintenance)` : ""}, ${s.unknown} unknown${s.unmonitored ? `, ${s.unmonitored} missing from Zabbix` : ""}`}
          to={`/customers/${s.code}/devices`}
        />
        <Stat label="Open tickets" value={s.tickets} tone={s.late ? "bad" : "ok"} note={`${s.late} past due`} to={`/customers/${s.code}/work`} />
        <Stat
          label="Configuration"
          value={s.checked ? `${s.compliant} of ${s.checked}` : "not checked"}
          tone={s.backupFailed ? "bad" : s.checked > s.compliant ? "warn" : "ok"}
          note={`${s.lastBackup ? `Last backup ${ago(s.lastBackup, now)}` : "No backup yet"}${s.backupFailed ? `, ${s.backupFailed} failed` : ""}`}
          to={`/customers/${s.code}/devices`}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <Section title="Availability" description={s.sla ? <>Counted by Zabbix from availability problems of High or worse. {s.sla && <span>Last month {pct(s.sla.previous, 3)}.</span>}</> : undefined}>
          <div className="space-y-4 p-4">
            {s.sla ? <BudgetMeter sla={s.sla} /> : <p className="text-[13px] text-muted-foreground">The customer has no availability agreement in Zabbix yet. It is created with the customer's set-up.</p>}
            <DowntimeColumns days={data.downtimeByDay} />
          </div>
        </Section>

        <Section title="Needs attention" description="From every tool, worst first">
          {attention.length ? (
            <ul className="divide-y">
              {attention
                .sort((a, b) => (a.tone === b.tone ? 0 : a.tone === "bad" ? -1 : 1))
                .map((a, i) => (
                  <li key={i} className="flex items-start gap-2.5 px-4 py-2.5 text-[13px]">
                    <StateDot tone={a.tone} className="mt-1.5" />
                    <div className="min-w-0 flex-1">
                      <div>{a.text}</div>
                      <div className="text-[12px] text-muted-foreground">{a.where}</div>
                    </div>
                    {a.to && (
                      <Link to={a.to} className="shrink-0 text-link" aria-label="Open">
                        <ArrowUpRight className="size-4" />
                      </Link>
                    )}
                    {a.href && a.tool && (
                      <ToolLink tool={a.tool} href={a.href} className="shrink-0">
                        <ArrowUpRight className="size-4" />
                      </ToolLink>
                    )}
                  </li>
                ))}
            </ul>
          ) : (
            <Empty>Nothing needs attention.</Empty>
          )}
        </Section>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Planned" description="Maintenance windows and changes still to come" actions={<Link to={`/customers/${s.code}/changes`} className="text-[13px] text-link hover:underline">All</Link>}>
          {next.length ? (
            <ul className="divide-y text-[13px]">
              {next.map((n) => (
                <li key={n.key} className="px-4 py-2.5">
                  <div className="font-medium">
                    {n.title} {n.on && <span className="ml-1 text-[12px] font-medium text-info">on now</span>}
                  </div>
                  <div className="text-[12px] text-muted-foreground">{n.sub}</div>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>Nothing planned.</Empty>
          )}
        </Section>
        <Section title="Recent console actions" actions={<Link to={`/customers/${s.code}/activity`} className="text-[13px] text-link hover:underline">All</Link>}>
          {data.audit.length ? (
            <ul className="divide-y text-[13px]">
              {data.audit.slice(0, 5).map((e) => (
                <li key={e.id} className="flex items-start justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="truncate">
                      <b className="font-medium">{e.action}</b> {e.target}
                    </div>
                    <div className="text-[12px] text-muted-foreground">
                      {e.name || e.email}, {ago(e.at, now)}
                      {e.ticket ? `, ${e.ticket}` : ""}
                    </div>
                  </div>
                  <OutcomePill outcome={e.outcome} />
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No actions from the console yet.</Empty>
          )}
        </Section>
      </div>
    </div>
  );
}
