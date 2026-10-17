/* Problems, tickets, changes, maintenance windows and tasks: the lists more than one page shows. */
import { CirclePause, ScanSearch, UserRoundCheck } from "lucide-react";
import { Link } from "react-router";
import type { Change, Maintenance, Problem, Task, Ticket } from "@/lib/api";
import { ago, duration, since, timeOnly, when, whenFull, zoneLabel } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { PRIORITY_NAMES } from "@/lib/status";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useActions } from "@/components/actions/actions";
import { Empty, TableWrap } from "@/components/page";
import { OutcomePill, Pill, SeverityEdge, SeverityTag } from "@/components/status";
import { ToolLink } from "@/components/tool-link";

const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-3 py-2 align-top";

export function ProblemList({ problems, showCustomer = true, empty = "No open problems." }: { problems: Problem[]; showCustomer?: boolean; empty?: string }) {
  const actions = useActions();
  const now = useNow(30_000);
  if (!problems.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="divide-y">
      {problems.map((p) => {
        const last = p.updates[0];
        return (
          <li key={p.eventid} className={cn("relative flex flex-col gap-2 py-3 pl-5 pr-4 md:flex-row md:items-center md:gap-4", p.suppressed && "bg-muted/40")}>
            <SeverityEdge severity={p.severity} muted={p.acknowledged || p.suppressed} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <SeverityTag severity={p.severity} />
                <ToolLink tool="Zabbix" href={p.link} className="font-semibold text-foreground">
                  {p.name}
                </ToolLink>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px] text-muted-foreground">
                <Link to={`/customers/${p.customer}/devices/${p.device}`} className="font-medium text-foreground hover:underline">
                  {p.device}
                </Link>
                <span>{p.site}</span>
                {showCustomer && (
                  <Link to={`/customers/${p.customer}`} className="hover:text-foreground hover:underline">
                    {p.customerName}
                  </Link>
                )}
                {p.opdata && <span className="num">{p.opdata}</span>}
                {p.ticketId ? (
                  <ToolLink tool="GLPI" href={p.ticketLink} className="text-[13px]">
                    Ticket #{p.ticketId}
                  </ToolLink>
                ) : (
                  <span>no ticket</span>
                )}
              </div>
              <div className="mt-1 text-[12.5px]">
                {p.suppressed ? (
                  <span className="inline-flex items-center gap-1 text-info">
                    <CirclePause className="size-3.5" /> In maintenance: no alerts sent
                  </span>
                ) : p.acknowledged && last ? (
                  <span className="inline-flex max-w-full items-start gap-1 text-muted-foreground">
                    <UserRoundCheck className="mt-px size-3.5 shrink-0 text-ok" />
                    <span className="truncate">
                      {last.who}, {ago(last.at, now)}: {last.message}
                    </span>
                  </span>
                ) : (
                  <span className="font-medium text-warn">Not acknowledged</span>
                )}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2 md:flex-col md:items-end md:gap-1.5">
              <span className="num text-[13px] font-medium md:text-right" title={`Started ${whenFull(p.started)}`}>
                {since(p.started, now)}
              </span>
              {actions.canAct && (
                <div className="ml-auto flex gap-1.5 md:ml-0">
                  <Button size="xs" variant={p.acknowledged ? "ghost" : "outline"} onClick={() => actions.acknowledge(p)}>
                    {p.acknowledged ? "Add note" : "Acknowledge"}
                  </Button>
                  <Button size="xs" variant="outline" onClick={() => actions.investigate(p)}>
                    <ScanSearch /> Investigate
                  </Button>
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function DueText({ ticket, now }: { ticket: Ticket; now: number }) {
  if (!ticket.due) return <span className="text-muted-foreground">no due date</span>;
  const ms = Date.parse(ticket.due) - now;
  if (ms < 0) return <Pill tone="bad" title={`Due ${whenFull(ticket.due)}`}>{duration(-ms / 1000)} late</Pill>;
  return (
    <span className={cn("num", ms < 3600_000 && "font-medium text-warn")} title={`Due ${whenFull(ticket.due)}`}>
      in {duration(ms / 1000)}
    </span>
  );
}

export function TicketTable({ tickets, showCustomer = true, customerNames, empty = "No open tickets." }: { tickets: Ticket[]; showCustomer?: boolean; customerNames?: Record<string, string>; empty?: string }) {
  const now = useNow(60_000);
  if (!tickets.length) return <Empty>{empty}</Empty>;
  return (
    <TableWrap>
      <table className="w-full min-w-[720px] text-[13px]">
        <thead className="border-b bg-muted/40">
          <tr>
            <th className={th}>Ticket</th>
            <th className={th}>Title</th>
            {showCustomer && <th className={th}>Customer</th>}
            <th className={th}>Priority</th>
            <th className={th}>Status</th>
            <th className={th}>Due</th>
            <th className={th}>Technician</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {tickets.map((t) => (
            <tr key={t.id} className={cn(t.late && "bg-bad-bg/40")}>
              <td className={cn(td, "whitespace-nowrap")}>
                <ToolLink tool="GLPI" href={t.link}>#{t.id}</ToolLink>
                <div className="text-[12px] text-muted-foreground">{t.type}</div>
              </td>
              <td className={td}>
                <div className="font-medium">{t.name}</div>
                <div className="text-[12px] text-muted-foreground">
                  {t.source}, opened {ago(t.opened, now)}
                </div>
              </td>
              {showCustomer && (
                <td className={td}>
                  <Link to={`/customers/${t.customer}`} className="hover:underline">
                    {customerNames?.[t.customer] ?? t.customer}
                  </Link>
                </td>
              )}
              <td className={cn(td, "whitespace-nowrap")}>{PRIORITY_NAMES[t.priority]}</td>
              <td className={cn(td, "whitespace-nowrap")}>{t.status}</td>
              <td className={cn(td, "whitespace-nowrap")}>
                <DueText ticket={t} now={now} />
              </td>
              <td className={cn(td, "whitespace-nowrap")}>{t.technician || <span className="text-muted-foreground">{t.group}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function TicketDigest({ tickets, customerNames }: { tickets: Ticket[]; customerNames: Record<string, string> }) {
  const now = useNow(60_000);
  if (!tickets.length) return <Empty>No open tickets.</Empty>;
  return (
    <ul className="divide-y">
      {tickets.map((t) => (
        <li key={t.id} className="flex items-start justify-between gap-3 px-4 py-2.5 text-[13px]">
          <div className="min-w-0">
            <div className="truncate font-medium">
              <ToolLink tool="GLPI" href={t.link} className="mr-1.5">#{t.id}</ToolLink>
              {t.name}
            </div>
            <div className="truncate text-[12px] text-muted-foreground">
              {customerNames[t.customer] ?? t.customer}, {t.status.toLowerCase()}
              {t.technician ? `, ${t.technician}` : ", not taken yet"}
            </div>
          </div>
          <div className="shrink-0 text-right text-[12.5px]">
            <DueText ticket={t} now={now} />
          </div>
        </li>
      ))}
    </ul>
  );
}

const FINISHED = ["Applied", "Closed", "Review", "Refused", "Cancelled"];

export function changeTone(status: string) {
  if (status === "Accepted") return "ok" as const;
  if (["Approval", "Evaluation", "New", "Pending"].includes(status)) return "warn" as const;
  if (FINISHED.includes(status)) return "neutral" as const;
  return "info" as const;
}

export function windowText(start: string | null, end: string | null): string {
  if (!start || !end) return "not planned yet";
  return `${when(start)} to ${timeOnly(end)} ${zoneLabel()}`;
}

export function ChangeTable({ changes, customerNames, showCustomer = true }: { changes: Change[]; customerNames?: Record<string, string>; showCustomer?: boolean }) {
  if (!changes.length) return <Empty>No changes.</Empty>;
  const now = Date.now();
  return (
    <TableWrap>
      <table className="w-full min-w-[760px] text-[13px]">
        <thead className="border-b bg-muted/40">
          <tr>
            <th className={th}>Change</th>
            <th className={th}>What</th>
            {showCustomer && <th className={th}>Customer</th>}
            <th className={th}>Window</th>
            <th className={th}>Status</th>
            <th className={th}>Automation</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {changes.map((c) => {
            const running = c.plannedStart && c.plannedEnd && Date.parse(c.plannedStart) <= now && Date.parse(c.plannedEnd) >= now && !FINISHED.includes(c.status);
            return (
              <tr key={c.id} className={cn(FINISHED.includes(c.status) && "text-muted-foreground")}>
                <td className={cn(td, "whitespace-nowrap")}>
                  <ToolLink tool="GLPI" href={c.link}>#{c.id}</ToolLink>
                  <div className="text-[12px] text-muted-foreground">{c.risk} risk</div>
                </td>
                <td className={td}>
                  <div className="font-medium">{c.name}</div>
                  <div className="text-[12px] text-muted-foreground">
                    {c.devices.length ? c.devices.join(", ") : "no devices named"}, requested by {c.requester}
                  </div>
                </td>
                {showCustomer && (
                  <td className={td}>
                    <Link to={`/customers/${c.customer}/changes`} className="hover:underline">
                      {customerNames?.[c.customer] ?? c.customer}
                    </Link>
                  </td>
                )}
                <td className={cn(td, "whitespace-nowrap")}>
                  {windowText(c.plannedStart, c.plannedEnd)}
                  {running && <div className="text-[12px] font-medium text-info">in progress now</div>}
                </td>
                <td className={cn(td, "whitespace-nowrap")}>
                  <Pill tone={changeTone(c.status)}>{c.status}</Pill>
                </td>
                <td className={td}>{c.template ? <span>{c.template}, when accepted</span> : <span className="text-muted-foreground">none, done by hand</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function MaintenanceTable({ windows, customerNames, showCustomer = true }: { windows: Maintenance[]; customerNames?: Record<string, string>; showCustomer?: boolean }) {
  if (!windows.length) return <Empty>No maintenance windows.</Empty>;
  return (
    <TableWrap>
      <table className="w-full min-w-[720px] text-[13px]">
        <thead className="border-b bg-muted/40">
          <tr>
            <th className={th}>Window</th>
            <th className={th}>Name</th>
            {showCustomer && <th className={th}>Customer</th>}
            <th className={th}>Devices</th>
            <th className={th}>Data</th>
            <th className={th}>State</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {windows.map((m) => (
            <tr key={m.id} className={cn(m.state === "ended" && "text-muted-foreground")}>
              <td className={cn(td, "whitespace-nowrap")}>{windowText(m.start, m.end)}</td>
              <td className={td}>
                <ToolLink tool="Zabbix" href={m.link} className="font-medium text-foreground">{m.name}</ToolLink>
                <div className="text-[12px] text-muted-foreground">
                  set by {m.createdBy}
                  {m.changeId ? `, change #${m.changeId}` : ""}
                </div>
              </td>
              {showCustomer && (
                <td className={td}>
                  <Link to={`/customers/${m.customer}/changes`} className="hover:underline">
                    {customerNames?.[m.customer] ?? m.customer}
                  </Link>
                </td>
              )}
              <td className={td}>{m.devices === null ? "every device" : m.devices.join(", ")}</td>
              <td className={cn(td, "whitespace-nowrap")}>{m.collectData ? "collected" : "not collected"}</td>
              <td className={cn(td, "whitespace-nowrap")}>
                <Pill tone={m.state === "active" ? "info" : m.state === "upcoming" ? "neutral" : "neutral"} icon={m.state === "active" ? CirclePause : undefined}>
                  {m.state === "active" ? "on now" : m.state}
                </Pill>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function TaskTable({ tasks, empty = "No tasks yet." }: { tasks: Task[]; empty?: string }) {
  const now = useNow(30_000);
  if (!tasks.length) return <Empty>{empty}</Empty>;
  return (
    <TableWrap>
      <table className="w-full min-w-[680px] text-[13px]">
        <thead className="border-b bg-muted/40">
          <tr>
            <th className={th}>Task</th>
            <th className={th}>Template and devices</th>
            <th className={th}>For</th>
            <th className={th}>State</th>
            <th className={th}>Started</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {tasks.map((t) => (
            <tr key={t.id}>
              <td className={cn(td, "whitespace-nowrap")}>
                <ToolLink tool="Semaphore" href={t.link}>#{t.id}</ToolLink>
              </td>
              <td className={td}>
                <div className="font-medium">{t.template}</div>
                <div className="text-[12px] text-muted-foreground">
                  {t.devices.join(", ")}, by {t.by}
                </div>
              </td>
              <td className={cn(td, "whitespace-nowrap")}>{t.item}</td>
              <td className={cn(td, "whitespace-nowrap")}>
                <OutcomePill outcome={t.status} title={t.message} />
              </td>
              <td className={cn(td, "whitespace-nowrap")} title={whenFull(t.created)}>
                {ago(t.created, now)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
