/* One device across the tools: Nautobot's record, Zabbix's values and problems, Golden Config's
   compliance and backups, Semaphore's runs, GLPI's tickets. */
import { ChevronDown, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import type { ComplianceRule, MetricChart, TimeRange } from "@/lib/api";
import { ago, bits, speed, whenFull } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { useDevice, useMetrics } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useActions } from "@/components/actions/actions";
import { TimeSeriesCard } from "@/components/charts/time-series";
import { Empty, ErrorBlock, FreshnessNote, LoadingBlock, Page, PageHeader, Section, TableWrap } from "@/components/page";
import { HostPill, Pill } from "@/components/status";
import { ToolLink } from "@/components/tool-link";
import { MaintenanceTable, ProblemList, TaskTable, TicketTable } from "@/components/work-items";
import { openTool } from "./customer/layout";

const RANGES: { value: TimeRange; label: string }[] = [
  { value: "1h", label: "1 h" },
  { value: "6h", label: "6 h" },
  { value: "24h", label: "24 h" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
];

/* The template triggers' default thresholds, drawn as a reference line on the chart. */
function thresholdFor(chart: MetricChart): { value: number; label: string } | undefined {
  if (chart.id === "icmp_rtt") return { value: 150, label: "Trigger 150 ms" };
  if (chart.id === "icmp_loss") return { value: 20, label: "Trigger 20 %" };
  if (chart.id === "cpu" || chart.id === "memory") return { value: 90, label: "Trigger 90 %" };
  if (chart.id.startsWith("fs:")) return { value: 95, label: "Trigger 95 %" };
  return undefined;
}

const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-3 py-2 align-top";

function RuleDiff({ rule }: { rule: ComplianceRule }) {
  return (
    <div className="grid gap-2 md:grid-cols-2">
      <div className="min-w-0 rounded-md border">
        <div className="border-b bg-ok-bg/60 px-3 py-1.5 text-[12px] font-medium text-ok">Intended, missing on the device</div>
        <pre className="scroll-x px-3 py-2 text-[12px] leading-relaxed">
          {rule.missing.map((l, i) => (
            <div key={i}>
              <span className="select-none text-ok">+ </span>
              {l}
            </div>
          ))}
        </pre>
      </div>
      <div className="min-w-0 rounded-md border">
        <div className="border-b bg-bad-bg/60 px-3 py-1.5 text-[12px] font-medium text-bad">On the device, not intended</div>
        <pre className="scroll-x px-3 py-2 text-[12px] leading-relaxed">
          {rule.extra.map((l, i) => (
            <div key={i}>
              <span className="select-none text-bad">- </span>
              {l}
            </div>
          ))}
        </pre>
      </div>
    </div>
  );
}

export function DevicePage() {
  const { code = "", name = "" } = useParams();
  const { data, error, isLoading } = useDevice(code, name);
  const actions = useActions();
  const now = useNow(60_000);
  const [range, setRange] = useState<TimeRange>("6h");
  const [iface, setIface] = useState<string | null>(null);

  const charts = data?.charts ?? [];
  const core = useMemo(() => charts.filter((c) => c.units !== "bps"), [charts]);
  const ifaceCharts = useMemo(() => charts.filter((c) => c.units === "bps"), [charts]);
  const chosen = ifaceCharts.find((c) => c.interface === iface) ?? ifaceCharts[0];
  const wanted = useMemo(() => [...core.map((c) => c.id), ...(chosen ? [chosen.id] : [])], [core, chosen]);
  const metrics = useMetrics(data?.device.id, range, wanted);

  if (isLoading) return <LoadingBlock />;
  if (error || !data) return <ErrorBlock error={error} />;
  const d = data.device;
  const failedRules = data.compliance.filter((r) => !r.compliant);
  const backupFailed = d.backup?.lastAttempt && d.backup.lastSuccess && d.backup.lastAttempt > d.backup.lastSuccess;
  const metricById = new Map((metrics.data?.charts ?? []).map((c) => [c.id, c]));

  return (
    <Page>
      <PageHeader
        crumbs={[
          { label: "Customers", to: "/customers" },
          { label: data.customerName, to: `/customers/${code}` },
          { label: "Devices", to: `/customers/${code}/devices` },
        ]}
        title={d.name}
        badge={d.host ? <HostPill state={d.host.state} maintenance={d.host.maintenance} title={d.host.error || undefined} /> : <Pill tone="neutral" icon={null}>{d.status === "Active" ? (d.monitoring ? "not in Zabbix" : "not monitored") : d.status.toLowerCase()}</Pill>}
        meta={
          <>
            <span className="capitalize">{d.role}</span>
            <span>{d.platform}, {d.model}</span>
            <span>{d.site}</span>
            {d.ip && <span className="font-mono text-[12.5px]">{d.ip}</span>}
            <span>serial {d.serial}</span>
          </>
        }
        actions={
          <>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <ExternalLink /> Open in <ChevronDown className="opacity-60" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuLabel className="text-[12px] font-normal text-muted-foreground">Opens signed in, on this device</DropdownMenuLabel>
                <DropdownMenuItem onSelect={() => openTool(d.links.nautobot, "Nautobot")}>Nautobot: the device record</DropdownMenuItem>
                {d.links.zabbix && <DropdownMenuItem onSelect={() => openTool(d.links.zabbix!, "Zabbix")}>Zabbix: latest data</DropdownMenuItem>}
                {d.links.backups && <DropdownMenuItem onSelect={() => openTool(d.links.backups!, "Forgejo")}>Forgejo: configuration backup</DropdownMenuItem>}
              </DropdownMenuContent>
            </DropdownMenu>
            {actions.canAct && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="sm">
                    Actions <ChevronDown className="opacity-70" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-60">
                  {actions.canRun && (
                    <DropdownMenuItem onSelect={() => actions.playbook({ customer: code, devices: [d.name] })}>Run a playbook on it</DropdownMenuItem>
                  )}
                  <DropdownMenuItem onSelect={() => actions.maintenance({ customer: code, devices: [d.name] })}>Plan maintenance</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => actions.ticket({ customer: code, kind: "Incident", devices: [d.name], title: `${d.name}: ` })}>Open a ticket</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => actions.ticket({ customer: code, kind: "Change", devices: [d.name] })}>Open a change</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => actions.configCheck(code, data.customerName)}>Configuration check (customer)</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </>
        }
      />

      <div className="space-y-5">
        {data.problems.length > 0 && (
          <Section title="Problems on this device">
            <ProblemList problems={data.problems} showCustomer={false} />
          </Section>
        )}

        <Section
          title="Monitoring"
          description={
            d.host
              ? metrics.data?.source === "trends"
                ? "Zabbix trends: hourly averages"
                : "Zabbix history: the values as collected"
              : undefined
          }
          actions={
            d.host && (
              <div className="flex flex-wrap items-center gap-2">
                <ToggleGroup type="single" variant="outline" size="sm" value={range} onValueChange={(v) => v && setRange(v as TimeRange)} aria-label="Time range">
                  {RANGES.map((r) => (
                    <ToggleGroupItem key={r.value} value={r.value} className="px-2.5 text-[12.5px]">
                      {r.label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </div>
            )
          }
        >
          {!d.host ? (
            <Empty>
              {d.status !== "Active"
                ? "The device is not active in Nautobot, so Zabbix does not monitor it."
                : d.monitoring
                  ? "Not in Zabbix yet. Check its address and platform in Nautobot, then re-sync the customer."
                  : "Monitoring is switched off for this device in Nautobot."}
            </Empty>
          ) : (
            <div className="space-y-4 p-4">
              <div className="grid gap-4 lg:grid-cols-2">
                {core.map((info) => {
                  const chart = metricById.get(info.id);
                  return chart ? (
                    <TimeSeriesCard key={info.id} chart={chart} range={range} threshold={thresholdFor(chart)} dim={metrics.isFetching} />
                  ) : (
                    <div key={info.id} className="h-[262px] rounded-lg border bg-card" />
                  );
                })}
              </div>
              {ifaceCharts.length > 0 && chosen && (
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2 text-[13px]">
                    <span className="text-muted-foreground">Interface</span>
                    <Select value={chosen.interface} onValueChange={setIface}>
                      <SelectTrigger size="sm" className="w-64" aria-label="Interface">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ifaceCharts.map((c) => {
                          const port = data.interfaces.find((i) => i.name === c.interface);
                          return (
                            <SelectItem key={c.id} value={c.interface!}>
                              {c.interface}
                              {port?.description ? `: ${port.description}` : ""}
                            </SelectItem>
                          );
                        })}
                      </SelectContent>
                    </Select>
                  </div>
                  {metricById.get(chosen.id) ? (
                    <TimeSeriesCard chart={metricById.get(chosen.id)!} range={range} dim={metrics.isFetching} height={220} />
                  ) : (
                    <div className="h-[292px] rounded-lg border bg-card" />
                  )}
                </div>
              )}
            </div>
          )}
        </Section>

        <Section title="Interfaces" description="Cables and circuits from Nautobot, state and traffic from Zabbix">
          <TableWrap>
            <table className="w-full min-w-[860px] text-[13px]">
              <thead className="border-b bg-muted/40">
                <tr>
                  <th className={th}>Interface</th>
                  <th className={th}>State</th>
                  <th className={th}>Speed</th>
                  <th className={cn(th, "text-right")}>In</th>
                  <th className={cn(th, "text-right")}>Out</th>
                  <th className={cn(th, "text-right")}>Errors</th>
                  <th className={th}>Connected to</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.interfaces.map((i) => (
                  <tr key={i.name} className={cn(i.status === "admin-down" && "text-muted-foreground")}>
                    <td className={td}>
                      <span className="font-mono text-[12.5px]">{i.name}</span>
                      {i.description && <div className="text-[12px] text-muted-foreground">{i.description}</div>}
                    </td>
                    <td className={td}>
                      {i.status === "up" ? <Pill tone="ok">up</Pill> : i.status === "down" ? <Pill tone="bad">down</Pill> : <Pill tone="neutral" icon={null}>shut</Pill>}
                    </td>
                    <td className={cn(td, "whitespace-nowrap")}>{speed(i.speed)}</td>
                    <td className={cn(td, "num whitespace-nowrap text-right")}>{bits(i.inBps) || "none"}</td>
                    <td className={cn(td, "num whitespace-nowrap text-right")}>{bits(i.outBps) || "none"}</td>
                    <td className={cn(td, "num whitespace-nowrap text-right", (i.errorsPerMin ?? 0) > 2 && "font-medium text-bad")}>
                      {i.errorsPerMin === null ? "" : `${i.errorsPerMin} per min`}
                    </td>
                    <td className={td}>
                      {i.peer ? (
                        i.circuit ? (
                          <span>
                            IOH network, circuit <span className="font-mono text-[12.5px]">{i.circuit}</span>
                          </span>
                        ) : (
                          <span>
                            <Link to={`/customers/${code}/devices/${i.peer.device}`} className="font-medium hover:underline">
                              {i.peer.device}
                            </Link>{" "}
                            <span className="font-mono text-[12.5px]">{i.peer.interface}</span>
                          </span>
                        )
                      ) : (
                        <span className="text-muted-foreground">no cable</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Section>

        <div className="grid gap-5 xl:grid-cols-2">
          <Section
            title="Configuration"
            description={
              d.check
                ? `Golden Config compares the device with its intended configuration every night${d.check.checked ? `; last check ${ago(d.check.checked, now)}` : ""}.`
                : undefined
            }
            actions={d.driftTicket && <ToolLink tool="GLPI" href={d.links.driftTicket} className="text-[13px]">Drift ticket #{d.driftTicket}</ToolLink>}
          >
            {!d.gcScope ? (
              <Empty>Not in Golden Config: it looks after active network devices with an address and a supported platform.</Empty>
            ) : !data.compliance.length ? (
              <Empty>Not checked yet. The first check runs tonight, or start one from the customer's actions.</Empty>
            ) : (
              <div className="space-y-3 p-4">
                <div className="flex flex-wrap gap-1.5">
                  {data.compliance.map((r) => (
                    <Pill key={r.rule} tone={r.compliant ? "ok" : "warn"}>{r.rule}</Pill>
                  ))}
                </div>
                {failedRules.length ? (
                  failedRules.map((r) => (
                    <div key={r.rule} className="space-y-2">
                      <div className="text-[13px] font-semibold">Rule {r.rule}</div>
                      <RuleDiff rule={r} />
                    </div>
                  ))
                ) : (
                  <p className="text-[13px] text-muted-foreground">Every rule matches the intended configuration.</p>
                )}
              </div>
            )}
          </Section>

          <Section
            title="Backups"
            description={d.backup ? "Saved to Forgejo by Golden Config; each one is a commit" : undefined}
            actions={backupFailed ? <Pill tone="bad" title={`Last attempt ${whenFull(d.backup!.lastAttempt)}`}>last attempt failed</Pill> : undefined}
          >
            {data.backups.length ? (
              <ul className="divide-y text-[13px]">
                {data.backups.map((b) => (
                  <li key={b.commit} className="flex items-center justify-between gap-3 px-4 py-2">
                    <div className="min-w-0">
                      <div className="font-medium" title={whenFull(b.at)}>{ago(b.at, now)}</div>
                      <div className="truncate text-[12px] text-muted-foreground">{b.changedLines ? `${b.changedLines} lines changed` : "no change"}</div>
                    </div>
                    <ToolLink tool="Forgejo" href={b.link} className="font-mono text-[12.5px]">
                      {b.commit}
                    </ToolLink>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No backups yet.</Empty>
            )}
          </Section>
        </div>

        <div className="space-y-5">
          <Section title="Tickets about this device">
            <TicketTable tickets={data.tickets} showCustomer={false} empty="No open tickets name this device." />
          </Section>
          <Section title="Automation on this device">
            <TaskTable tasks={data.tasks} empty="No tasks ran on this device." />
          </Section>
        </div>

        {data.maintenance.length > 0 && (
          <Section title="Maintenance windows">
            <MaintenanceTable windows={data.maintenance} showCustomer={false} />
          </Section>
        )}
        <FreshnessNote freshness={data.freshness} sources="Nautobot, Zabbix, Golden Config, Forgejo, GLPI and Semaphore" />
      </div>
    </Page>
  );
}
