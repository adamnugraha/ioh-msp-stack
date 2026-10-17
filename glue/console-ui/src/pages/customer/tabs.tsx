import { Play, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { ago, whenFull } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { useTopology } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useActions } from "@/components/actions/actions";
import { AuditTable } from "@/components/audit-table";
import { Empty, ErrorBlock, LoadingBlock, Section, TableWrap } from "@/components/page";
import { HostPill, Pill } from "@/components/status";
import { ToolLink } from "@/components/tool-link";
import { TopologyView } from "@/components/topology";
import { ChangeTable, MaintenanceTable, ProblemList, TaskTable, TicketTable } from "@/components/work-items";
import { useCustomerData } from "./layout";

const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-3 py-2 align-top";

export function CustomerDevices() {
  const data = useCustomerData();
  const actions = useActions();
  const now = useNow(60_000);
  const [filter, setFilter] = useState("");
  const rows = useMemo(
    () => data.devices.filter((d) => !filter || `${d.name} ${d.site} ${d.role} ${d.platform} ${d.ip} ${d.serial}`.toLowerCase().includes(filter.toLowerCase())),
    [data.devices, filter],
  );
  return (
    <Section
      title="Devices"
      description="Nautobot is the source of truth; monitoring from Zabbix, configuration from Golden Config, backups in Forgejo."
      actions={
        <>
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter devices" className="h-8 w-48" aria-label="Filter devices" />
          {actions.canAct && (
            <Button size="sm" variant="outline" onClick={() => actions.addDevice({ customer: data.summary.code })}>
              <Plus /> Add a device
            </Button>
          )}
        </>
      }
    >
      <TableWrap>
        <table className="w-full min-w-[1000px] text-[13px]">
          <thead className="border-b bg-muted/40">
            <tr>
              <th className={th}>Device</th>
              <th className={th}>Site</th>
              <th className={th}>Role and platform</th>
              <th className={th}>Status</th>
              <th className={th}>Address</th>
              <th className={th}>Monitoring</th>
              <th className={th}>Configuration</th>
              <th className={th}>Last backup</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((d) => {
              const backupFailed = d.backup?.lastAttempt && d.backup.lastSuccess && d.backup.lastAttempt > d.backup.lastSuccess;
              return (
                <tr key={d.id} className={cn("hover:bg-accent/30", d.status !== "Active" && "text-muted-foreground")}>
                  <td className={td}>
                    <Link to={`/customers/${d.customer}/devices/${d.name}`} className="font-semibold text-foreground hover:underline">
                      {d.name}
                    </Link>
                    <div className="text-[12px] text-muted-foreground">serial {d.serial}</div>
                  </td>
                  <td className={td}>{d.site}</td>
                  <td className={td}>
                    <span className="capitalize">{d.role}</span>, {d.platform}
                    <div className="text-[12px] text-muted-foreground">{d.model}</div>
                  </td>
                  <td className={td}>{d.status}</td>
                  <td className={cn(td, "font-mono text-[12.5px]")}>{d.ip || <span className="font-sans text-muted-foreground">none</span>}</td>
                  <td className={td}>
                    {d.host ? (
                      <HostPill state={d.host.state} maintenance={d.host.maintenance} title={d.host.error || undefined} />
                    ) : !d.monitoring ? (
                      <Pill tone="neutral" icon={null} title="Monitoring switched off in Nautobot">off by choice</Pill>
                    ) : d.status === "Active" ? (
                      <Pill tone="warn" title="Active and monitoring wanted, but not in Zabbix yet. Check the address and platform, then re-sync.">not in Zabbix</Pill>
                    ) : (
                      <span className="text-[12.5px]">not active</span>
                    )}
                  </td>
                  <td className={td}>
                    {d.check ? (
                      d.check.failed.length ? (
                        <Pill tone="warn" title={d.check.failed.join(", ")}>
                          {d.check.failed.length} of {d.check.rules} rules differ
                        </Pill>
                      ) : (
                        <Pill tone="ok">compliant</Pill>
                      )
                    ) : d.gcScope ? (
                      <span className="text-[12.5px] text-muted-foreground">not checked yet</span>
                    ) : (
                      <span className="text-[12.5px] text-muted-foreground" title="Golden Config looks after active network devices with an address and a supported platform">
                        not in scope
                      </span>
                    )}
                    {d.driftTicket && (
                      <div className="mt-0.5 text-[12px]">
                        <ToolLink tool="GLPI" href={d.links.driftTicket}>
                          ticket #{d.driftTicket}
                        </ToolLink>
                      </div>
                    )}
                  </td>
                  <td className={cn(td, "whitespace-nowrap")}>
                    {backupFailed ? (
                      <Pill tone="bad" title={`Last attempt ${whenFull(d.backup!.lastAttempt)}`}>failed</Pill>
                    ) : d.backup?.lastSuccess ? (
                      <span title={whenFull(d.backup.lastSuccess)}>{ago(d.backup.lastSuccess, now)}</span>
                    ) : (
                      <span className="text-muted-foreground">none</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && <Empty>No device matches.</Empty>}
      </TableWrap>
    </Section>
  );
}

export function CustomerNetwork() {
  const data = useCustomerData();
  const { data: topo, error, isLoading } = useTopology(data.summary.code);
  return (
    <Section title="Network map" description="Cables and circuits recorded in Nautobot, with each device's state in Zabbix.">
      <div className="p-4">
        {isLoading ? <LoadingBlock /> : error || !topo ? <ErrorBlock error={error} /> : topo.links.length ? <TopologyView topo={topo} customer={data.summary.code} /> : <Empty>No cables recorded in Nautobot for this customer yet.</Empty>}
      </div>
    </Section>
  );
}

export function CustomerWork() {
  const data = useCustomerData();
  const actions = useActions();
  return (
    <div className="space-y-5">
      <Section title="Problems" description="Open in Zabbix on this customer's devices">
        <ProblemList problems={data.problems} showCustomer={false} />
      </Section>
      <Section
        title="Open tickets"
        description="Not solved or closed in GLPI, past due first"
        actions={actions.canAct && (
          <Button size="sm" variant="outline" onClick={() => actions.ticket({ customer: data.summary.code, kind: "Incident" })}>
            <Plus /> Open a ticket
          </Button>
        )}
      >
        <TicketTable tickets={data.tickets} showCustomer={false} />
      </Section>
    </div>
  );
}

export function CustomerChanges() {
  const data = useCustomerData();
  const actions = useActions();
  return (
    <div className="space-y-5">
      <Section
        title="Changes"
        description="GLPI changes of this customer. An accepted standard change can run its playbook on its own."
        actions={actions.canAct && (
          <Button size="sm" variant="outline" onClick={() => actions.ticket({ customer: data.summary.code, kind: "Change" })}>
            <Plus /> Open a change
          </Button>
        )}
      >
        <ChangeTable changes={data.changes} showCustomer={false} />
      </Section>
      <Section
        title="Maintenance windows"
        description="Zabbix sends no alerts for the devices in a window"
        actions={actions.canAct && (
          <Button size="sm" variant="outline" onClick={() => actions.maintenance({ customer: data.summary.code })}>
            <Plus /> Plan maintenance
          </Button>
        )}
      >
        <MaintenanceTable windows={data.maintenance} showCustomer={false} />
      </Section>
    </div>
  );
}

export function CustomerAutomation() {
  const data = useCustomerData();
  const actions = useActions();
  const a = data.automation;
  if (!a.projectId) {
    return (
      <Section title="Automation">
        <Empty>This customer has no Semaphore project yet. Re-sync the customer to create it.</Empty>
      </Section>
    );
  }
  return (
    <div className="grid gap-5 xl:grid-cols-[400px_minmax(0,1fr)]">
      <Section title="Templates" description="Ansible playbooks in the customer's Semaphore project">
        <ul className="divide-y">
          {a.templates.map((t) => (
            <li key={t.id} className="flex items-start justify-between gap-3 px-4 py-3 text-[13px]">
              <div className="min-w-0">
                <div className="font-semibold">{t.name}</div>
                <div className="text-muted-foreground">{t.description}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {!t.limit ? (
                    <Pill tone="warn" title="Semaphore would run it on every device of the customer: allow the limit to be set per run in the template">
                      cannot be limited to devices
                    </Pill>
                  ) : t.changeOnly ? (
                    <Pill tone="neutral" icon={null}>needs an approved change</Pill>
                  ) : (
                    <Pill tone="neutral" icon={null}>read-only</Pill>
                  )}
                </div>
              </div>
              {actions.canRun && t.limit && (
                <Button size="sm" variant="outline" onClick={() => actions.playbook({ customer: data.summary.code, template: t.name })}>
                  <Play /> Run
                </Button>
              )}
            </li>
          ))}
        </ul>
        {!actions.canRun && <p className="border-t px-4 py-3 text-[12.5px] text-muted-foreground">Starting playbooks needs the automation role.</p>}
      </Section>
      <Section title="Recent tasks" actions={<ToolLink tool="Semaphore" href={a.link} icon className="text-[13px]">All tasks in Semaphore</ToolLink>}>
        <TaskTable tasks={a.tasks} />
      </Section>
    </div>
  );
}

export function CustomerActivity() {
  const data = useCustomerData();
  return (
    <Section
      title="Console actions for this customer"
      description="Who did what from the console, and what came of it"
      actions={<Link to={`/audit?customer=${data.summary.code}`} className="text-[13px] text-primary hover:underline">Full audit trail</Link>}
    >
      <AuditTable entries={data.audit} showCustomer={false} />
    </Section>
  );
}
