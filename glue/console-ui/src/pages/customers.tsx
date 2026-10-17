/* Every managed customer: what they buy, how they are doing, and whether their set-up is complete. */
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { ago, pct } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { useCustomers } from "@/lib/queries";
import { severityName, severityTone } from "@/lib/status";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { ErrorBlock, LoadingBlock, Page, PageHeader, Section, TableWrap } from "@/components/page";
import { Pill, toneText } from "@/components/status";
import { BudgetMeter } from "@/components/charts/service-charts";

const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-3 py-2.5 align-top";

export function CustomersPage() {
  const { data, error, isLoading } = useCustomers();
  const [filter, setFilter] = useState("");
  const now = useNow(60_000);
  const rows = useMemo(
    () => (data ?? []).filter((c) => !filter || `${c.name} ${c.code} ${c.customerId}`.toLowerCase().includes(filter.toLowerCase())),
    [data, filter],
  );
  if (isLoading) return <LoadingBlock />;
  if (error || !data) return <ErrorBlock error={error} />;
  return (
    <Page>
      <PageHeader
        title="Customers"
        meta={<span>{data.length} managed customers. Worst state first.</span>}
      />
      <Section
        title="All customers"
        description="Customers come from Nautobot: a tenant in the managed services group with a customer code. msp-glue sets up the rest in every tool."
        actions={<Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by name or code" className="h-8 w-56" aria-label="Filter customers" />}
      >
        <TableWrap>
          <table className="w-full min-w-[1040px] text-[13px]">
            <thead className="border-b bg-muted/40">
              <tr>
                <th className={th}>Customer</th>
                <th className={th}>Set-up</th>
                <th className={th}>Devices</th>
                <th className={th}>Problems</th>
                <th className={th}>Tickets</th>
                <th className={cn(th, "w-[260px]")}>Availability this month</th>
                <th className={th}>Configuration</th>
                <th className={th}>Last backup</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((c) => (
                <tr key={c.code} className="hover:bg-accent/30">
                  <td className={td}>
                    <Link to={`/customers/${c.code}`} className="font-semibold hover:underline">{c.name}</Link>
                    <div className="text-[12px] text-muted-foreground">
                      {c.code}, {c.customerId}, <span className="capitalize">{c.tier}</span>{c.regulated ? ", regulated" : ""}
                    </div>
                  </td>
                  <td className={td}>
                    {c.provisioning === "provisioned" ? (
                      <Pill tone="ok">ready</Pill>
                    ) : (
                      <Pill tone={c.provisioning === "error" ? "bad" : "warn"} title={c.provisioningMessage}>{c.provisioning}</Pill>
                    )}
                  </td>
                  <td className={cn(td, "num")}>
                    {c.active} active of {c.devices}
                    <div className={cn("text-[12px]", c.down ? toneText("bad") : "text-muted-foreground")}>
                      {c.down ? `${c.down} down` : `${c.up} up`}
                      {c.unmonitored ? `, ${c.unmonitored} not in Zabbix` : ""}
                    </div>
                  </td>
                  <td className={td}>
                    {c.problems ? (
                      <Link to={`/customers/${c.code}/work`} className="hover:underline">
                        <span className={cn("font-medium", toneText(severityTone(c.worst)))}>{c.problems} open</span>
                        <div className="text-[12px] text-muted-foreground">worst {severityName(c.worst!).toLowerCase()}</div>
                      </Link>
                    ) : (
                      <span className="text-muted-foreground">none</span>
                    )}
                  </td>
                  <td className={td}>
                    {c.tickets ? `${c.tickets} open` : <span className="text-muted-foreground">none</span>}
                    {c.late > 0 && <div className={cn("text-[12px] font-medium", toneText("bad"))}>{c.late} past due</div>}
                  </td>
                  <td className={td}>
                    {c.sla && c.sla.sli !== null ? (
                      <>
                        <div className="flex items-baseline justify-between gap-2">
                          <span className={cn("num font-semibold", c.sla.sli < c.sla.slo && toneText("bad"))}>{pct(c.sla.sli, 3)}</span>
                          <span className="text-[12px] text-muted-foreground">target {c.sla.slo} %</span>
                        </div>
                        <BudgetMeter sla={c.sla} className="mt-1" compact />
                      </>
                    ) : (
                      <span className="text-muted-foreground">no agreement in Zabbix yet</span>
                    )}
                  </td>
                  <td className={td}>
                    {c.checked ? (
                      <>
                        {c.compliant} of {c.checked} compliant
                        {c.checked > c.compliant && <div className={cn("text-[12px]", toneText("warn"))}>{c.checked - c.compliant} drifted</div>}
                      </>
                    ) : (
                      <span className="text-muted-foreground">{c.inScope ? "not checked yet" : "nothing in scope"}</span>
                    )}
                  </td>
                  <td className={cn(td, "whitespace-nowrap")}>
                    {c.lastBackup ? ago(c.lastBackup, now) : <span className="text-muted-foreground">none</span>}
                    {c.backupFailed > 0 && <div className={cn("text-[12px] font-medium", toneText("bad"))}>{c.backupFailed} failed</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Section>
    </Page>
  );
}
