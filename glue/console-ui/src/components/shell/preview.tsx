/* Shown only in the demo build: the data is made up, and what the kit will add to make it real. */
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const PLAN: { area: string; preview: string; kit: string }[] = [
  {
    area: "Pages",
    preview: "Overview, customers, one page per customer with devices, network map, problems and tickets, automation and activity; one page per device; changes and maintenance; service levels; audit trail; tools and sign-in.",
    kit: "The same React pages, served by the console container. The 1.1 addresses keep working, so the links in chat alerts still open the right customer.",
  },
  {
    area: "Data",
    preview: "Sample customers and devices in the page.",
    kit: "An interface in the console (/api/v1) that reuses the 1.1 loaders for Nautobot, Zabbix, GLPI, Semaphore, Golden Config and msp-glue, with the same 45-second cache.",
  },
  {
    area: "Charts",
    preview: "Response time, loss, CPU, memory, disks and traffic per interface, with history up to 24 hours and trends beyond.",
    kit: "Zabbix history.get and trend.get for the device's items; the table view of every chart comes from the same answer.",
  },
  {
    area: "Network map",
    preview: "Cables and circuits per customer, coloured by Zabbix state.",
    kit: "Nautobot cables and circuit terminations (GraphQL), joined with the Zabbix host state.",
  },
  {
    area: "Actions",
    preview: "Acknowledge, investigate, run a playbook, open a ticket or change, plan maintenance, add a device, re-sync, configuration check. They follow the console's rules and change the sample only.",
    kit: "New: GLPI ticket and change creation, Zabbix maintenance.create, Nautobot device creation. Unchanged: the 1.1 actions and their checks. Every action is in the audit trail.",
  },
  {
    area: "Investigate",
    preview: "Written-out sample answers for the sample problems. No model is called.",
    kit: "Off until a model endpoint is set (Ollama, vLLM, Netra or any OpenAI-compatible server). The console reads the tools with its own read-only accounts; secrets in configuration backups are masked before anything is sent.",
  },
  {
    area: "Single sign-on",
    preview: "The tools menu and the tools page show how each tool signs people in.",
    kit: "Keycloak clients for Nautobot, Forgejo, Semaphore and Grafana (OpenID Connect), then Zabbix (SAML). For GLPI the method is chosen during the lab work. Keycloak roles map to each tool's own groups.",
  },
  {
    area: "Not in the preview",
    preview: "Real sign-in, CSV downloads, opening the tools, and anything that reaches the platform.",
    kit: "All of it, tested in the lab before release.",
  },
];

export function PreviewBanner() {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-preview-border bg-preview text-preview-foreground">
      <div className="gutter flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-[12.5px]">
        <FlaskConical className="size-4 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1">
          <b className="font-semibold">Preview with sample data.</b> The customers, devices and numbers are made up; actions change the sample only and nothing reaches the tools.
        </span>
        <Button size="xs" variant="outline" className="border-preview-border bg-transparent text-preview-foreground hover:bg-preview-border/40" onClick={() => setOpen(true)}>
          What kit 1.2.0 adds
        </Button>
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[calc(100dvh-1.5rem)] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>What the preview shows, and what kit 1.2.0 adds</DialogTitle>
            <DialogDescription>
              The preview is the console's real front end with a sample data source. The kit swaps that source for the console's API; the pages stay the same.
            </DialogDescription>
          </DialogHeader>
          <div className="scroll-x">
            <table className="w-full min-w-[560px] text-[13px]">
              <thead className="text-left text-muted-foreground">
                <tr>
                  <th className="w-28 py-1.5 pr-3 font-medium"></th>
                  <th className="py-1.5 pr-3 font-medium">In this preview</th>
                  <th className="py-1.5 font-medium">In the kit</th>
                </tr>
              </thead>
              <tbody>
                {PLAN.map((p) => (
                  <tr key={p.area} className="border-t align-top">
                    <th className="py-2 pr-3 text-left font-semibold">{p.area}</th>
                    <td className="py-2 pr-3">{p.preview}</td>
                    <td className="py-2">{p.kit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[12.5px] text-muted-foreground">
            The user menu has a "Preview as" switch to see the console as a manager, an engineer, or an engineer with the automation role.
          </p>
        </DialogContent>
      </Dialog>
    </div>
  );
}
