/* Operations: what is broken across every customer, and the place to act on it. */
import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import type { Overview } from "@/lib/api";
import { ago, plural, since } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { useMe, useOverview, useRefreshAll } from "@/lib/queries";
import { SEVERITY_NAMES } from "@/lib/status";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { CustomerMatrix } from "@/components/customer-matrix";
import { ErrorBlock, FreshnessNote, LoadingBlock, Page, PageHeader, Section, Stat } from "@/components/page";
import { OutcomePill, Pill } from "@/components/status";
import { ProblemList, TicketDigest, windowText } from "@/components/work-items";

function SeverityCounts({ data }: { data: Overview }) {
  const counts = [5, 4, 3, 2, 1].map((s) => ({ s, n: data.problems.filter((p) => p.severity === s).length })).filter((x) => x.n);
  if (!counts.length) return <>No open problems</>;
  return (
    <span className="flex flex-wrap gap-x-2.5 gap-y-0.5">
      {counts.map(({ s, n }) => (
        <span key={s} className="inline-flex items-center gap-1">
          <span aria-hidden className="size-2 rounded-[2px]" style={{ background: `var(--sev-${s})` }} />
          {n} {SEVERITY_NAMES[s].toLowerCase()}
        </span>
      ))}
    </span>
  );
}

export function OverviewPage() {
  const { data, error, isLoading, isFetching } = useOverview();
  const { data: me } = useMe();
  const refresh = useRefreshAll();
  const now = useNow(30_000);
  const [customer, setCustomer] = useState("all");
  const [hideAck, setHideAck] = useState(false);

  const names = useMemo(() => Object.fromEntries((data?.customers ?? []).map((c) => [c.code, c.name])), [data]);
  const problems = useMemo(
    () => (data?.problems ?? []).filter((p) => (customer === "all" || p.customer === customer) && (!hideAck || !p.acknowledged)),
    [data, customer, hideAck],
  );

  if (isLoading) return <LoadingBlock label="Reading every tool" />;
  if (error || !data) return <ErrorBlock error={error} />;

  const monitored = data.customers.reduce((s, c) => s + c.up + c.down + c.unknown, 0);
  const down = data.customers.reduce((s, c) => s + c.down, 0);
  const inMaintenance = data.customers.reduce((s, c) => s + c.downInMaintenance, 0);
  const unacked = data.problems.filter((p) => !p.acknowledged && !p.suppressed);
  const oldest = unacked.reduce<string | null>((o, p) => (!o || p.started < o ? p.started : o), null);
  const allTickets = data.customers.reduce((s, c) => s + c.tickets, 0);
  const late = data.customers.reduce((s, c) => s + c.late, 0);
  const setupErrors = data.customers.filter((c) => c.provisioning === "error");
  const onTarget = data.customers.filter((c) => c.sla && c.sla.sli !== null && c.sla.sli >= c.sla.slo).length;
  const withSla = data.customers.filter((c) => c.sla && c.sla.sli !== null).length;

  return (
    <Page>
      <PageHeader
        title="Operations"
        meta={<FreshnessNote freshness={data.freshness} sources="Nautobot, Zabbix, GLPI, Golden Config, Semaphore and msp-glue" />}
        actions={
          <Button variant="outline" size="sm" onClick={refresh} disabled={isFetching}>
            <RefreshCw className={isFetching ? "animate-spin" : undefined} /> Refresh
          </Button>
        }
      />

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Open problems" value={data.problems.length} tone={data.problems.some((p) => p.severity >= 4 && !p.suppressed) ? "bad" : data.problems.length ? "warn" : "ok"} note={<SeverityCounts data={data} />} />
        <Stat
          label="Not acknowledged"
          value={unacked.length}
          tone={unacked.length ? "warn" : "ok"}
          note={oldest ? `Oldest waiting ${since(oldest, now)}` : "Everyone is on it"}
        />
        <Stat
          label="Devices down"
          value={down}
          tone={down - inMaintenance > 0 ? "bad" : "ok"}
          note={`of ${monitored} monitored${inMaintenance ? `, ${inMaintenance} in maintenance` : ""}`}
        />
        <Stat label="Tickets past due" value={late} tone={late ? "bad" : "ok"} note={`${allTickets} open in GLPI`} />
        <Stat
          label="Changes today"
          value={data.changesToday.length}
          note={`${plural(data.maintenance.filter((m) => m.state === "active").length, "maintenance window", "maintenance windows")} on now`}
          to="/changes"
        />
        {me?.canAct && data.glue ? (
          <Stat
            label="Integration"
            value={data.glue.lastError ? "1 error" : "OK"}
            tone={data.glue.lastError ? "warn" : "ok"}
            note={`Queue ${data.glue.queue}, last full check ${ago(data.glue.lastReconcile, now)}`}
          />
        ) : (
          <Stat label="On availability target" value={`${onTarget} of ${withSla}`} tone={onTarget < withSla ? "bad" : "ok"} note="This month so far" to="/service" />
        )}
      </div>

      {setupErrors.map((c) => (
        <div key={c.code} className="mb-5 flex flex-col gap-2 rounded-lg border border-bad/30 bg-bad-bg px-4 py-3 text-[13px] text-bad sm:flex-row sm:items-center sm:justify-between">
          <span>
            <b className="font-semibold">{c.name}:</b> {c.provisioningMessage}
          </span>
          <Link to={`/customers/${c.code}`} className="shrink-0 font-medium underline-offset-2 hover:underline">
            Open the customer
          </Link>
        </div>
      ))}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-5">
          <Section
            title="Problems"
            description="Worst and newest first. Acknowledged problems and those in maintenance move down."
            actions={
              <div className="flex flex-wrap items-center gap-3">
                <Select value={customer} onValueChange={setCustomer}>
                  <SelectTrigger size="sm" className="w-48" aria-label="Customer">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All customers</SelectItem>
                    {data.customers.map((c) => (
                      <SelectItem key={c.code} value={c.code}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <label className="flex items-center gap-2 text-[13px]">
                  <Switch checked={hideAck} onCheckedChange={setHideAck} /> Only new
                </label>
              </div>
            }
          >
            <ProblemList problems={problems} empty={hideAck || customer !== "all" ? "Nothing matches the filter." : "No open problems."} />
          </Section>


        </div>

        <div className="min-w-0 space-y-5">
          <Section title="Tickets due next" description="Past due first, then by due time" actions={<Link to="/customers" className="text-[13px] text-primary hover:underline">By customer</Link>}>
            <TicketDigest tickets={data.tickets.slice(0, 8)} customerNames={names} />
          </Section>

          <Section title="Today" description="Changes planned in GLPI and maintenance windows in Zabbix" actions={<Link to="/changes" className="text-[13px] text-primary hover:underline">All changes</Link>}>
            <ul className="divide-y text-[13px]">
              {data.maintenance.map((m) => (
                <li key={m.id} className="px-4 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium">{m.name}</span>
                    <Pill tone={m.state === "active" ? "info" : "neutral"} icon={null}>{m.state === "active" ? "on now" : "next"}</Pill>
                  </div>
                  <div className="text-[12px] text-muted-foreground">
                    Maintenance, {names[m.customer]}, {windowText(m.start, m.end)}
                  </div>
                </li>
              ))}
              {data.changesToday
                .filter((ch) => !data.maintenance.some((m) => m.changeId === ch.id))
                .map((ch) => (
                  <li key={ch.id} className="px-4 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">Change #{ch.id}: {ch.name}</span>
                      <Pill tone={ch.status === "Accepted" ? "ok" : "warn"} icon={null}>{ch.status.toLowerCase()}</Pill>
                    </div>
                    <div className="text-[12px] text-muted-foreground">
                      {names[ch.customer]}, {windowText(ch.plannedStart, ch.plannedEnd)}
                    </div>
                  </li>
                ))}
              {!data.maintenance.length && !data.changesToday.length && <li className="px-4 py-6 text-center text-muted-foreground">Nothing planned today.</li>}
            </ul>
          </Section>

          {me?.canAct && data.glue && (
            <Section title="Integration jobs" description="msp-glue keeps the tools in step with Nautobot">
              <div className="space-y-3 px-4 py-3 text-[13px]">
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <div className="text-[12px] text-muted-foreground">Waiting</div>
                    <div className="font-medium">{plural(data.glue.queue, "job", "jobs")}</div>
                  </div>
                  <div>
                    <div className="text-[12px] text-muted-foreground">Last full check</div>
                    <div className="font-medium">{ago(data.glue.lastReconcile, now)}</div>
                  </div>
                </div>
                {data.glue.lastError && (
                  <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-warn">
                    Last error {ago(data.glue.lastError.at, now)}: {data.glue.lastError.message}
                  </div>
                )}
                <ul className="divide-y rounded-md border">
                  {data.glue.jobs.slice(0, 5).map((j) => (
                    <li key={j.id} className="flex items-start justify-between gap-2 px-3 py-2">
                      <div className="min-w-0">
                        <div className="truncate font-medium">{j.name}</div>
                        <div className="truncate text-[12px] text-muted-foreground">{j.result || "…"}</div>
                      </div>
                      <div className="shrink-0 text-right">
                        <OutcomePill outcome={j.status} />
                        <div className="mt-0.5 text-[11.5px] text-muted-foreground">{ago(j.started, now)}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            </Section>
          )}
        </div>
      </div>

      <div className="mt-5">
        <Section title="Customers" description="Each customer as every tool sees it. Tinted cells need attention; each opens the place to deal with it.">
            <CustomerMatrix customers={data.customers} />
          </Section>
      </div>
    </Page>
  );
}
