/* Changes and maintenance: what GLPI has planned and what Zabbix will keep quiet, over the next week. */
import { CalendarClock, CirclePause, Plus } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router";
import { dayLabel, isToday, sameDay, timeOnly, zoneLabel } from "@/lib/format";
import { useCustomers, useWork } from "@/lib/queries";
import { Button } from "@/components/ui/button";
import { useActions } from "@/components/actions/actions";
import { Empty, ErrorBlock, FreshnessNote, LoadingBlock, Page, PageHeader, Section } from "@/components/page";
import { Pill } from "@/components/status";
import { ChangeTable, changeTone, MaintenanceTable } from "@/components/work-items";

interface AgendaItem {
  key: string;
  start: number;
  end: number;
  kind: "change" | "maintenance";
  title: string;
  customer: string;
  status: string;
  devices: string;
}

export function ChangesPage() {
  const { data, error, isLoading } = useWork();
  const { data: customers } = useCustomers();
  const actions = useActions();
  const names = useMemo(() => Object.fromEntries((customers ?? []).map((c) => [c.code, c.name])), [customers]);

  const agenda = useMemo(() => {
    if (!data) return [];
    const now = Date.now();
    const until = now + 7 * 86_400_000;
    const items: AgendaItem[] = [];
    for (const m of data.maintenance) {
      const s = Date.parse(m.start);
      const e = Date.parse(m.end);
      if (e < now || s > until) continue;
      items.push({ key: m.id, start: s, end: e, kind: "maintenance", title: m.name, customer: m.customer, status: m.state === "active" ? "on now" : "planned", devices: m.devices === null ? "every device" : m.devices.join(", ") });
    }
    for (const c of data.changes) {
      if (!c.plannedStart || !c.plannedEnd || ["Applied", "Closed", "Review", "Refused", "Cancelled"].includes(c.status)) continue;
      const s = Date.parse(c.plannedStart);
      const e = Date.parse(c.plannedEnd);
      if (e < now || s > until) continue;
      if (c.maintenanceId && data.maintenance.some((m) => m.id === c.maintenanceId)) continue;
      items.push({ key: `c${c.id}`, start: s, end: e, kind: "change", title: `Change #${c.id}: ${c.name}`, customer: c.customer, status: c.status, devices: c.devices.join(", ") });
    }
    return items.sort((a, b) => a.start - b.start);
  }, [data]);

  if (isLoading) return <LoadingBlock />;
  if (error || !data) return <ErrorBlock error={error} />;

  const days: { day: number; items: AgendaItem[] }[] = [];
  for (const item of agenda) {
    const last = days[days.length - 1];
    if (last && sameDay(last.day, item.start)) last.items.push(item);
    else days.push({ day: item.start, items: [item] });
  }

  return (
    <Page>
      <PageHeader
        title="Changes and maintenance"
        meta={<FreshnessNote freshness={data.freshness} sources="GLPI changes and Zabbix maintenance" />}
        actions={
          actions.canAct && (
            <>
              <Button size="sm" variant="outline" onClick={() => actions.maintenance()}>
                <CalendarClock /> Plan maintenance
              </Button>
              <Button size="sm" onClick={() => actions.ticket({ kind: "Change" })}>
                <Plus /> Open a change
              </Button>
            </>
          )
        }
      />
      <div className="space-y-5">
        <Section title="Next seven days" description={`Times in ${zoneLabel()}. A change with its own maintenance window is shown once, as the window.`}>
          {days.length ? (
            <div className="divide-y">
              {days.map((d) => (
                <div key={d.day} className="grid gap-2 px-4 py-3 md:grid-cols-[150px_minmax(0,1fr)]">
                  <div className="text-[13px] font-semibold">
                    {dayLabel(d.day)}
                    {isToday(d.day) && <span className="ml-1.5 text-[12px] font-medium text-info">today</span>}
                  </div>
                  <ul className="space-y-2">
                    {d.items.map((it) => (
                      <li key={it.key} className="flex flex-col gap-1 rounded-md border px-3 py-2 text-[13px] sm:flex-row sm:items-center sm:gap-3">
                        <span className="num shrink-0 font-medium sm:w-28">
                          {timeOnly(it.start)} to {timeOnly(it.end)}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="font-medium">{it.title}</span>
                          <span className="block text-[12px] text-muted-foreground">
                            <Link to={`/customers/${it.customer}/changes`} className="hover:underline">{names[it.customer] ?? it.customer}</Link>, {it.devices || "no devices named"}
                          </span>
                        </span>
                        {it.kind === "maintenance" ? (
                          <Pill tone={it.status === "on now" ? "info" : "neutral"} icon={CirclePause}>maintenance {it.status}</Pill>
                        ) : (
                          <Pill tone={changeTone(it.status)}>{it.status}</Pill>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <Empty>Nothing planned in the next seven days.</Empty>
          )}
        </Section>
        <Section title="Changes" description="Open changes first, then the finished ones">
          <ChangeTable changes={data.changes} customerNames={names} />
        </Section>
        <Section title="Maintenance windows" description="Zabbix sends no alerts and opens no tickets for the devices in a window">
          <MaintenanceTable windows={data.maintenance} customerNames={names} />
        </Section>
      </div>
    </Page>
  );
}
