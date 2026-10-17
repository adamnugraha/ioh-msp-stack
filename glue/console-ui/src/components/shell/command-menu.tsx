import { Building2, CalendarClock, Monitor, Moon, Plus, Server, Siren, Sun } from "lucide-react";
import { useNavigate } from "react-router";
import { useAllDevices, useCustomers, useMe, useOverview } from "@/lib/queries";
import { severityName } from "@/lib/status";
import { useTheme } from "@/lib/theme";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { useActions } from "@/components/actions/actions";
import { NAV } from "./nav";

/** Ctrl K: go to any page, customer, device or open problem, or start an action. */
export function CommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const { data: customers } = useCustomers();
  const { data: devices } = useAllDevices(open);
  const { data: overview } = useOverview();
  const { data: me } = useMe();
  const { setChoice } = useTheme();
  const actions = useActions();
  const go = (to: string) => {
    onOpenChange(false);
    navigate(to);
  };
  const run = (f: () => void) => {
    onOpenChange(false);
    // let the search dialog finish closing before the next dialog opens
    setTimeout(f, 60);
  };
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Search the console" description="Pages, customers, devices, open problems and actions">
      <CommandInput placeholder="Customer, device, problem or action" />
      <CommandList className="max-h-[min(460px,70dvh)]">
        <CommandEmpty>Nothing matches. Device names and customer codes work best.</CommandEmpty>
        {(overview?.problems.length ?? 0) > 0 && (
          <CommandGroup heading="Open problems">
            {overview!.problems.slice(0, 8).map((p) => (
              <CommandItem key={p.eventid} value={`problem ${p.device} ${p.name} ${p.customerName}`} onSelect={() => go(`/customers/${p.customer}/devices/${p.device}`)}>
                <Siren style={{ color: `var(--sev-${p.severity})` }} />
                <span className="truncate">
                  <b className="font-medium">{p.device}</b> {p.name}
                </span>
                <span className="ml-auto shrink-0 text-[12px] text-muted-foreground">{severityName(p.severity)}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        <CommandGroup heading="Customers">
          {(customers ?? []).map((c) => (
            <CommandItem key={c.code} value={`customer ${c.name} ${c.code} ${c.customerId}`} onSelect={() => go(`/customers/${c.code}`)}>
              <Building2 />
              <span>{c.name}</span>
              <span className="ml-auto text-[12px] text-muted-foreground">{c.code}</span>
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandGroup heading="Devices">
          {(devices ?? []).map((d) => (
            <CommandItem key={d.name} value={`device ${d.name} ${d.ip} ${d.site} ${d.customerName}`} onSelect={() => go(`/customers/${d.customer}/devices/${d.name}`)}>
              <Server />
              <span className="font-medium">{d.name}</span>
              <span className="truncate text-muted-foreground">{d.site}</span>
              <span className="ml-auto shrink-0 font-mono text-[11.5px] text-muted-foreground">{d.ip}</span>
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Pages">
          {NAV.map((n) => (
            <CommandItem key={n.to} value={`page ${n.label}`} onSelect={() => go(n.to)}>
              <n.icon />
              {n.label}
            </CommandItem>
          ))}
        </CommandGroup>
        {me?.canAct && (
          <CommandGroup heading="New">
            <CommandItem value="new ticket incident request" onSelect={() => run(() => actions.ticket({ kind: "Incident" }))}><Plus /> GLPI ticket</CommandItem>
            <CommandItem value="new change" onSelect={() => run(() => actions.ticket({ kind: "Change" }))}><Plus /> GLPI change</CommandItem>
            <CommandItem value="new maintenance window zabbix" onSelect={() => run(() => actions.maintenance())}><CalendarClock /> Zabbix maintenance window</CommandItem>
            <CommandItem value="new device nautobot add" onSelect={() => run(() => actions.addDevice())}><Server /> Device in Nautobot</CommandItem>
          </CommandGroup>
        )}
        <CommandGroup heading="Theme">
          <CommandItem value="theme dark" onSelect={() => run(() => setChoice("dark"))}><Moon /> Dark</CommandItem>
          <CommandItem value="theme light" onSelect={() => run(() => setChoice("light"))}><Sun /> Light</CommandItem>
          <CommandItem value="theme system" onSelect={() => run(() => setChoice("system"))}><Monitor /> As the system</CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
