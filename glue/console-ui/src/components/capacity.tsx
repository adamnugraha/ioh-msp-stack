/* msp-glue's link capacity forecast (kit 1.6.0): each link's busy hour against its commit rate, the trend over
   four weeks and the days left before the warning level. One GLPI ticket per link at risk. */
import { Link } from "react-router";
import type { CapacityLink, CapacityStatus } from "@/lib/api";
import { bits } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Empty, TableWrap } from "@/components/page";
import { Pill } from "@/components/status";
import { ToolLink } from "@/components/tool-link";
import { MiniTrend } from "@/components/trend";
import type { Tone } from "@/lib/status";

const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-3 py-2.5 align-top";

export const CAPACITY_LABEL: Record<CapacityStatus, { text: string; tone: Tone; hint: string }> = {
  over: { text: "above the warning level", tone: "bad", hint: "The last 7 busy hours average more than the warning level of the link's capacity." },
  at_risk: { text: "at risk", tone: "warn", hint: "The four-week trend reaches the warning level within the horizon." },
  ok: { text: "fine", tone: "ok", hint: "Below the warning level and not reaching it within the horizon." },
  too_little_data: { text: "too little data", tone: "neutral", hint: "Fewer than 14 days of traffic in Zabbix: no forecast yet." },
  no_capacity: { text: "no capacity known", tone: "neutral", hint: "Give the circuit a commit rate (or the interface a speed) in Nautobot." },
  no_data: { text: "no traffic data", tone: "neutral", hint: "Zabbix has no traffic items for this interface: check the interface name and the template." },
  not_monitored: { text: "not monitored", tone: "neutral", hint: "The device is not in Zabbix." },
};

export function CapacityTable({ links, showCustomer, warn = 80, empty }: { links: CapacityLink[]; showCustomer?: boolean; warn?: number; empty?: string }) {
  if (!links.length) return <Empty>{empty ?? "No link to show."}</Empty>;
  return (
    <TableWrap>
      <table className="w-full min-w-[920px] text-[13px]">
        <thead className="border-b bg-muted/40">
          <tr>
            {showCustomer && <th className={th}>Customer</th>}
            <th className={th}>Link</th>
            <th className={th}>Capacity</th>
            <th className={th}>Busy hour, last 7 days</th>
            <th className={th}>Four weeks</th>
            <th className={th}>Forecast</th>
            <th className={th}>Ticket</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {links.map((l) => {
            const status = CAPACITY_LABEL[l.status];
            const limit = l.capacityBps ? (warn / 100) * l.capacityBps : null;
            return (
              <tr key={`${l.customer}|${l.device}|${l.interface}`} className={cn(l.status === "over" && "bg-bad-bg/40")}>
                {showCustomer && (
                  <td className={td}>
                    <Link to={`/customers/${l.customer}`} className="font-semibold hover:underline">{l.customerName}</Link>
                  </td>
                )}
                <td className={td}>
                  <Link to={`/customers/${l.customer}/devices/${l.device}`} className="font-semibold hover:underline">{l.device}</Link>{" "}
                  <span className="font-mono text-[12.5px]">{l.interface}</span>
                  <div className="text-[12px] text-muted-foreground">
                    {l.circuit ? `Circuit ${l.circuit}${l.provider ? `, ${l.provider}` : ""}` : l.description || "Described as a WAN link"}
                    {l.site ? `, ${l.site}` : ""}
                  </div>
                </td>
                <td className={cn(td, "num whitespace-nowrap")}>
                  {l.capacityBps ? bits(l.capacityBps) : <span className="text-muted-foreground">unknown</span>}
                  {l.capacityFrom && <div className="text-[12px] text-muted-foreground">from the {l.capacityFrom === "circuit" ? "commit rate" : l.capacityFrom === "interface" ? "interface speed" : "device"}</div>}
                </td>
                <td className={cn(td, "num whitespace-nowrap")}>
                  {l.busyBps != null ? bits(l.busyBps) : <span className="text-muted-foreground">none</span>}
                  {l.utilisation != null && (
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-muted" aria-hidden>
                        <div
                          className={cn("h-full rounded-full", l.utilisation >= warn ? "bg-[var(--status-critical)]" : l.status === "at_risk" ? "bg-[var(--status-warning)]" : "bg-[var(--series-1)]")}
                          style={{ width: `${Math.min(100, l.utilisation)}%` }}
                        />
                      </div>
                      <span className="text-[12px]">{l.utilisation} %</span>
                    </div>
                  )}
                  {l.direction && <div className="text-[12px] text-muted-foreground">{l.direction === "in" ? "received" : "sent"} by the device</div>}
                </td>
                <td className={td}>
                  <MiniTrend points={l.history} limit={limit} tone={status.tone} label={`Busy hour of ${l.device} ${l.interface}, one point per day`} />
                  {l.slopeBpsPerDay != null && l.slopeBpsPerDay > 0 && <div className="text-[12px] text-muted-foreground">+{bits(l.slopeBpsPerDay)} a day</div>}
                </td>
                <td className={td}>
                  <Pill tone={status.tone} title={status.hint}>{status.text}</Pill>
                  {l.status === "at_risk" && l.daysToWarn != null && (
                    <div className="mt-1 text-[12px]">{warn} % in about {Math.round(l.daysToWarn)} days</div>
                  )}
                </td>
                <td className={cn(td, "whitespace-nowrap")}>
                  {l.ticketId ? <ToolLink tool="GLPI" href={l.ticketLink}>#{l.ticketId}</ToolLink> : <span className="text-muted-foreground">none</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}
