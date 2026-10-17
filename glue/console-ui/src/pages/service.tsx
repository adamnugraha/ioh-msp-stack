/* Service levels for managers: availability against each customer's target, ticket service levels,
   configuration compliance and backup freshness, for this month or last. */
import { Download } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { api, DEMO } from "@/lib/api";
import { downtime, hours, pct } from "@/lib/format";
import { useService } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { BudgetBars, Sparkline } from "@/components/charts/service-charts";
import { ErrorBlock, FreshnessNote, LoadingBlock, Page, PageHeader, Section, Stat, TableWrap } from "@/components/page";
import { Pill, toneText } from "@/components/status";

const th = "px-2.5 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-2.5 py-2.5 align-top";

export function ServicePage() {
  const [month, setMonth] = useState<string | undefined>(undefined);
  const { data, error, isLoading, isFetching } = useService(month);
  if (isLoading) return <LoadingBlock />;
  if (error || !data) return <ErrorBlock error={error} />;
  const t = data.totals;
  const below = data.rows.filter((r) => r.met === false);
  const exportCsv = () => {
    const url = api.exportUrl("service", { month: data.month });
    if (!url || DEMO) toast("Downloads service-" + data.month + ".csv", { description: "One row per customer, every column of the table. Not available in the preview." });
    else window.location.assign(url);
  };
  return (
    <Page>
      <PageHeader
        title="Service levels"
        meta={<FreshnessNote freshness={data.freshness} sources="Zabbix availability agreements, GLPI service levels and Golden Config" />}
        actions={
          <>
            <Select value={data.month} onValueChange={(v) => setMonth(v)}>
              <SelectTrigger size="sm" className="w-52" aria-label="Month">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {data.monthsAvailable.map((m) => (
                  <SelectItem key={m.month} value={m.month}>{m.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" onClick={exportCsv}>
              <Download /> Export CSV
            </Button>
          </>
        }
      />

      <div className={cn("space-y-5 transition-opacity", isFetching && "opacity-70")}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="On availability target" value={`${t.met} of ${t.withSla}`} tone={t.met < t.withSla ? "bad" : "ok"} note={below.length ? `Below: ${below.map((r) => r.name).join(", ")}` : "Every customer with an agreement"} />
          <Stat label="Tickets opened" value={t.opened} note={`${t.solved} solved, ${t.open} still open`} />
          <Stat label="Service level breaches" value={t.breaches} tone={t.breaches ? "warn" : "ok"} note="Taken late or resolved late, tickets opened this month" />
          <Stat label="Mean time to resolve" value={hours(t.mttrHours) || "none"} note="Solved tickets, weighted by count" />
          <Stat label="Devices compliant" value={t.compliance === null ? "none" : `${t.compliance} %`} tone={t.compliance !== null && t.compliance < 95 ? "warn" : "ok"} note="Network devices Golden Config checks" />
          <Stat label="Backups fresh" value={t.backup === null ? "none" : `${t.backup} %`} tone={t.backup !== null && t.backup < 95 ? "warn" : "ok"} note="Backed up in the last 26 hours" />
        </div>

        <Section title="Availability" description="Downtime counts while an availability problem of High or worse is open on any of the customer's devices.">
          <div className="grid gap-6 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <BudgetBars rows={data.rows} />
            <div className="min-w-0">
              <div className="mb-2 text-[13.5px] font-semibold">Below target</div>
              {below.length ? (
                <ul className="space-y-2 text-[13px]">
                  {below.map((r) => (
                    <li key={r.code} className="rounded-md border border-bad/30 bg-bad-bg/60 px-3 py-2">
                      <Link to={`/customers/${r.code}`} className="font-semibold hover:underline">{r.name}</Link>
                      <div>
                        {pct(r.sla?.sli, 3)} against a target of {r.sla?.slo} %: down {downtime(r.sla?.downtime)} this month.
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[13px] text-muted-foreground">Every customer with an agreement is on target.</p>
              )}
              <p className="mt-4 text-[12.5px] leading-relaxed text-muted-foreground">
                Targets come from each customer's tier in Nautobot: gold 99.9 %, silver 99.5 %, bronze 99.0 %. This month's figure counts the month so far, so a long
                outage early in the month weighs more now than it will at the end.
              </p>
            </div>
          </div>
        </Section>

        <Section title="By customer">
          <TableWrap>
            <table className="w-full min-w-[1100px] text-[13px]">
              <thead className="border-b bg-muted/40">
                <tr>
                  <th className={th}>Customer</th>
                  <th className={th}>Target</th>
                  <th className={th}>{data.label}</th>
                  <th className={th}>Last month</th>
                  <th className={th}>Six months</th>
                  <th className={th}>Downtime</th>
                  <th className={th}>Tickets</th>
                  <th className={th}>Breaches</th>
                  <th className={th}>Time to resolve</th>
                  <th className={th}>Compliant</th>
                  <th className={th}>Backups</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map((r) => (
                  <tr key={r.code} className={cn(r.met === false && "bg-bad-bg/40")}>
                    <td className={td}>
                      <Link to={`/customers/${r.code}`} className="font-semibold hover:underline">{r.name}</Link>
                      <div className="text-[12px] capitalize text-muted-foreground">
                        {r.tier}
                        {r.regulated ? ", regulated" : ""}
                      </div>
                    </td>
                    <td className={cn(td, "num whitespace-nowrap")}>{r.sla ? `${r.sla.slo} %` : <span className="text-muted-foreground">none</span>}</td>
                    <td className={td}>
                      {r.sla?.sli != null ? (
                        <>
                          <div className={cn("num font-semibold", r.met === false && toneText("bad"))}>{pct(r.sla.sli, 3)}</div>
                          {r.met === false ? <Pill tone="bad" className="mt-1">below target</Pill> : <Pill tone="ok" className="mt-1">on target</Pill>}
                        </>
                      ) : (
                        <span className="text-muted-foreground">no agreement yet</span>
                      )}
                    </td>
                    <td className={cn(td, "num whitespace-nowrap")}>{r.sla?.previous != null ? pct(r.sla.previous, 3) : ""}</td>
                    <td className={td}>
                      <Sparkline values={r.history.map((h) => h.sli)} target={r.sla?.slo ?? null} />
                    </td>
                    <td className={cn(td, "num whitespace-nowrap")}>{r.sla ? downtime(r.sla.downtime) : ""}</td>
                    <td className={cn(td, "num whitespace-nowrap")}>
                      {r.opened} opened, {r.solved} solved
                      {r.openLate > 0 && <div className={cn("text-[12px]", toneText("bad"))}>{r.openLate} open past due</div>}
                    </td>
                    <td className={cn(td, "num whitespace-nowrap")}>
                      {r.breaches ? (
                        <span className={toneText("warn")}>
                          {r.breaches}
                          <span className="block text-[12px] text-muted-foreground">{r.ownLate} taken late, {r.resolveLate} resolved late</span>
                        </span>
                      ) : (
                        "none"
                      )}
                    </td>
                    <td className={cn(td, "num whitespace-nowrap")}>{hours(r.mttrHours) || <span className="text-muted-foreground">none solved</span>}</td>
                    <td className={cn(td, "num whitespace-nowrap")}>
                      {r.compliance === null ? <span className="text-muted-foreground">none</span> : `${r.compliance} %`}
                      {r.checkedN > 0 && <div className="text-[12px] text-muted-foreground">{r.compliantN} of {r.checkedN}</div>}
                    </td>
                    <td className={cn(td, "num whitespace-nowrap")}>
                      {r.backup === null ? <span className="text-muted-foreground">none</span> : `${r.backup} %`}
                      {r.netN > 0 && <div className="text-[12px] text-muted-foreground">{r.freshN} of {r.netN}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <p className="border-t px-4 py-2.5 text-[12.5px] text-muted-foreground">
            Six months: a grey line per customer, this month as a dot (red when below target) and the target as a thin line. The export has every number in this table.
          </p>
        </Section>
      </div>
    </Page>
  );
}
