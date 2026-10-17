/* One customer: the header with links into every tool and the actions, then one tab per job. */
import { ChevronDown, ExternalLink } from "lucide-react";
import { NavLink, Outlet, useOutletContext, useParams } from "react-router";
import { toast } from "sonner";
import { DEMO, type CustomerDetail, type ToolName } from "@/lib/api";
import { useCustomer } from "@/lib/queries";
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
import { useActions } from "@/components/actions/actions";
import { ErrorBlock, LoadingBlock, Page, PageHeader } from "@/components/page";
import { Pill } from "@/components/status";

export const useCustomerData = () => useOutletContext<CustomerDetail>();

const LINKS: { key: keyof CustomerDetail["links"]; tool: ToolName; label: string }[] = [
  { key: "nautobot", tool: "Nautobot", label: "Nautobot: tenant, sites, devices" },
  { key: "zabbix", tool: "Zabbix", label: "Zabbix: problems of the customer" },
  { key: "glpi", tool: "GLPI", label: "GLPI: tickets of the customer" },
  { key: "semaphore", tool: "Semaphore", label: "Semaphore: the customer's project" },
  { key: "forgejo", tool: "Forgejo", label: "Forgejo: configuration backups" },
  { key: "grafana", tool: "Grafana", label: "Grafana: the customer's dashboards" },
];

export function openTool(url: string, tool: string) {
  if (DEMO) toast(`Opens ${tool}, signed in with your Keycloak account`, { description: `${url} (the tools are not connected to the preview)` });
  else window.open(url, "_blank", "noopener,noreferrer");
}

export function OpenInMenu({ links }: { links: CustomerDetail["links"] }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          <ExternalLink /> Open in <ChevronDown className="opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel className="text-[12px] font-normal text-muted-foreground">Opens signed in, filtered to this customer</DropdownMenuLabel>
        {LINKS.filter((l) => links[l.key]).map((l) => (
          <DropdownMenuItem key={l.key} onSelect={() => openTool(links[l.key]!, l.tool)}>
            {l.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ActionsMenu({ data }: { data: CustomerDetail }) {
  const actions = useActions();
  const s = data.summary;
  if (!actions.canAct) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm">
          Actions <ChevronDown className="opacity-70" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuItem onSelect={() => actions.ticket({ customer: s.code, kind: "Incident" })}>Open a ticket</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => actions.ticket({ customer: s.code, kind: "Change" })}>Open a change</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => actions.maintenance({ customer: s.code })}>Plan maintenance</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => actions.addDevice({ customer: s.code })}>Add a device</DropdownMenuItem>
        {actions.canRun && <DropdownMenuItem onSelect={() => actions.playbook({ customer: s.code })}>Run a playbook</DropdownMenuItem>}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => actions.resync(s.code, s.name)}>Re-sync the customer</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => actions.configCheck(s.code, s.name)}>Run a configuration check</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function CustomerLayout() {
  const { code = "" } = useParams();
  const { data, error, isLoading } = useCustomer(code);
  if (isLoading) return <LoadingBlock />;
  if (error || !data) return <ErrorBlock error={error} />;
  const s = data.summary;
  const openChanges = data.changes.filter((c) => !["Applied", "Closed", "Review", "Refused", "Cancelled"].includes(c.status)).length;
  const tabs = [
    { to: `/customers/${code}`, label: "Overview", end: true },
    { to: `/customers/${code}/devices`, label: "Devices", count: data.devices.length },
    { to: `/customers/${code}/network`, label: "Network map" },
    { to: `/customers/${code}/work`, label: "Problems and tickets", count: data.problems.length + data.tickets.length },
    { to: `/customers/${code}/changes`, label: "Changes and maintenance", count: openChanges },
    { to: `/customers/${code}/automation`, label: "Automation" },
    { to: `/customers/${code}/activity`, label: "Activity" },
  ];
  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Customers", to: "/customers" }]}
        title={s.name}
        badge={<span className="text-[14px] text-muted-foreground">{s.code}</span>}
        meta={
          <>
            <Pill tone="neutral" icon={null}>{s.tier[0].toUpperCase() + s.tier.slice(1)} tier</Pill>
            {s.regulated && <Pill tone="warn" icon={null}>regulated</Pill>}
            {s.provisioning === "provisioned" ? (
              <Pill tone="ok">set-up ready</Pill>
            ) : (
              <Pill tone={s.provisioning === "error" ? "bad" : "warn"}>set-up {s.provisioning}</Pill>
            )}
            <span>Customer number {s.customerId}</span>
            <span>Contact {s.contactEmail}</span>
          </>
        }
        actions={
          <>
            <OpenInMenu links={data.links} />
            <ActionsMenu data={data} />
          </>
        }
      />
      {s.provisioning === "error" && (
        <div className="mb-4 rounded-lg border border-bad/30 bg-bad-bg px-4 py-3 text-[13px] text-bad">
          <b className="font-semibold">Set-up error.</b> {s.provisioningMessage}
        </div>
      )}
      <nav aria-label="Customer pages" className="scroll-x -mx-1 mb-5 border-b">
        <ul className="flex min-w-max gap-1 px-1">
          {tabs.map((t) => (
            <li key={t.to}>
              <NavLink
                to={t.to}
                end={t.end}
                className={({ isActive }) =>
                  cn(
                    "-mb-px inline-flex items-center gap-1.5 border-b-2 border-transparent px-3 py-2.5 text-[13.5px] font-medium text-muted-foreground transition-colors hover:text-foreground",
                    isActive && "border-primary text-foreground",
                  )
                }
              >
                {t.label}
                {t.count !== undefined && t.count > 0 && (
                  <span className="num rounded-full bg-secondary px-1.5 text-[11.5px] text-secondary-foreground">{t.count}</span>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <Outlet context={data} />
    </Page>
  );
}
