/* The actions an engineer starts from the console. Each dialog says which tool it changes and what is
   written where; the console checks the person's role and the tool's rules again on the server. */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import {
  api,
  type DeviceRequest,
  type DeviceRole,
  type MaintenanceRequest,
  type PlaybookRequest,
  type Problem,
  type TicketRequest,
} from "@/lib/api";
import { ago, duration, fromLocalInput, plural, since, timeOnly, toLocalInput, when, zoneLabel } from "@/lib/format";
import { useAction, useCustomer, useCustomers } from "@/lib/queries";
import { cn } from "@/lib/utils";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { SeverityTag } from "@/components/status";
import { DevicePicker, ErrorNote, Field, ItemPicker, Summary, type ItemChoice } from "./form";

const wide = "max-h-[calc(100dvh-1.5rem)] overflow-y-auto sm:max-w-xl";

function ProblemCard({ problem }: { problem: Problem }) {
  return (
    <div className="rounded-md border bg-muted/50 px-3 py-2.5 text-[13px]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <SeverityTag severity={problem.severity} />
        <span className="font-semibold">{problem.name}</span>
      </div>
      <div className="mt-1 text-muted-foreground">
        {problem.device} at {problem.site}, {problem.customerName}. Open for {since(problem.started)}
        {problem.opdata ? `. ${problem.opdata}` : ""}
        {problem.ticketId ? `. GLPI ticket #${problem.ticketId}` : ". No ticket"}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------- acknowledge
export function AckDialog({ problem, onClose }: { problem: Problem; onClose: () => void }) {
  const [note, setNote] = useState("");
  const action = useAction((text: string) => api.acknowledge(problem.eventid, text));
  const already = problem.acknowledged;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={wide}>
        <DialogHeader>
          <DialogTitle>{already ? "Add a note to the problem" : "Acknowledge the problem"}</DialogTitle>
          <DialogDescription>
            Zabbix records who is on it. {problem.ticketId ? `It adds the note to GLPI ticket #${problem.ticketId}` : "The problem has no GLPI ticket"}
            {problem.severity >= 4 ? ", and the NOC chat gets an update." : "."}
          </DialogDescription>
        </DialogHeader>
        <ProblemCard problem={problem} />
        {problem.updates.length > 0 && (
          <ul className="space-y-1.5 text-[13px]">
            {problem.updates.map((u, i) => (
              <li key={i} className="rounded-md border px-3 py-2">
                <span className="font-medium">{u.who}</span> <span className="text-muted-foreground">{ago(u.at)}</span>
                <div>{u.message}</div>
              </li>
            ))}
          </ul>
        )}
        <Field label={already ? "Note" : "Note (optional)"} htmlFor="ack-note" hint="Written to Zabbix with your name, as you type it here.">
          <Textarea id="ack-note" value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={500} placeholder="What you are doing about it" />
        </Field>
        <ErrorNote error={action.error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={action.isPending || (already && !note.trim())} onClick={() => action.mutate(note, { onSuccess: onClose })}>
            {action.isPending ? "Sending" : already ? "Add note" : "Acknowledge"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------- playbook
export interface PlaybookInit {
  customer: string;
  devices?: string[];
  template?: string;
  item?: ItemChoice;
  reason?: string;
}

export function PlaybookDialog({ init, onClose }: { init: PlaybookInit; onClose: () => void }) {
  const { data, isLoading } = useCustomer(init.customer);
  const templates = data?.automation.templates ?? [];
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [devices, setDevices] = useState<string[]>(init.devices ?? []);
  const [item, setItem] = useState<ItemChoice | null>(init.item ?? null);
  const [reason, setReason] = useState(init.reason ?? "");
  const [variables, setVariables] = useState("");
  const action = useAction((req: PlaybookRequest) => api.runPlaybook(req));

  useEffect(() => {
    if (!data || templateId !== null) return;
    const pick = templates.find((t) => t.name === init.template && t.limit) ?? templates.find((t) => t.limit);
    if (pick) setTemplateId(pick.id);
  }, [data, templateId, templates, init.template]);

  const template = templates.find((t) => t.id === templateId);
  useEffect(() => {
    if (template?.changeOnly && item?.type === "Ticket") setItem(null);
  }, [template, item]);

  const ready = template && devices.length > 0 && item && reason.trim().length >= 10;
  const submit = () => {
    if (!template || !item) return;
    action.mutate(
      { customer: init.customer, templateId: template.id, devices, itemType: item.type, itemId: item.id, reason, variables },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={wide}>
        <DialogHeader>
          <DialogTitle>Run a playbook</DialogTitle>
          <DialogDescription>
            Semaphore runs the template on the devices you choose, for {data?.summary.name ?? "the customer"}. The start and the result are added to the ticket or change.
          </DialogDescription>
        </DialogHeader>
        {isLoading && <p className="text-[13px] text-muted-foreground">Reading the customer's templates…</p>}
        {data && !data.automation.projectId && (
          <ErrorNote error="This customer has no Semaphore project yet. Re-sync the customer first." />
        )}
        {data && data.automation.projectId && (
          <div className="grid gap-4">
            <Field label="Template" hint={template?.description}>
              <Select value={templateId === null ? undefined : String(templateId)} onValueChange={(v) => setTemplateId(Number(v))}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Choose a template" />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)} disabled={!t.limit}>
                      {t.name}
                      {!t.limit ? " (cannot be limited to devices)" : t.changeOnly ? " (needs an approved change)" : " (read-only)"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {templates.some((t) => !t.limit) && (
              <p className="-mt-2 text-[12.5px] text-muted-foreground">
                A template that cannot be limited would run on every device of the customer, so the console does not start it. Allow the limit to be set per run in Semaphore to offer it here.
              </p>
            )}
            <Field label="Devices">
              <DevicePicker devices={data.devices} value={devices} onChange={setDevices} />
            </Field>
            <Field
              label={template?.changeOnly ? "For GLPI change (status Accepted)" : "For GLPI ticket or change"}
              hint={template?.changeOnly ? `'${template.name}' changes devices, so it runs only for an approved change.` : "Read-only templates accept an open ticket or change of this customer."}
            >
              <ItemPicker tickets={data.tickets} changes={data.changes} value={item} onChange={setItem} changesOnly={template?.changeOnly} />
            </Field>
            <Field label="Reason (goes into the ticket)" htmlFor="pb-reason">
              <Textarea id="pb-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} placeholder="Why you run it, at least 10 characters" />
            </Field>
            <Field label="Variables (optional)" htmlFor="pb-vars" hint="One 'name: value' per line. Ansible's own variables cannot be set here.">
              <Textarea id="pb-vars" rows={2} className="font-mono text-[12.5px]" value={variables} onChange={(e) => setVariables(e.target.value)} placeholder="interface: GigabitEthernet1/0/12" />
            </Field>
            {template && devices.length > 0 && item && (
              <Summary>
                Starts <b>{template.name}</b> now on {devices.join(", ")}. The console writes the start to {item.type.toLowerCase()} #{item.id} in GLPI, follows the task, and adds the
                result when it finishes. It is recorded in the audit trail under your name.
              </Summary>
            )}
          </div>
        )}
        <ErrorNote error={action.error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!ready || action.isPending} onClick={submit}>
            {action.isPending ? "Starting" : "Start playbook"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// -------------------------------------------------------------------------- ticket or change
export interface TicketInit {
  customer?: string;
  kind?: TicketRequest["kind"];
  devices?: string[];
  title?: string;
  description?: string;
  template?: string;
}

const CHANGE_TEMPLATES = ["Collect diagnostics", "Bounce interface", "Linux patching"];

function quarterFromNow(minutes: number) {
  const t = Date.now() + minutes * 60_000;
  return Math.ceil(t / 900_000) * 900_000;
}

function CustomerSelect({ value, onChange }: { value: string; onChange: (code: string) => void }) {
  const { data } = useCustomers();
  return (
    <Select value={value || undefined} onValueChange={onChange}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder="Choose the customer" />
      </SelectTrigger>
      <SelectContent>
        {(data ?? []).slice().sort((a, b) => a.name.localeCompare(b.name)).map((c) => (
          <SelectItem key={c.code} value={c.code}>
            {c.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function TicketDialog({ init, onClose }: { init: TicketInit; onClose: () => void }) {
  const [customer, setCustomer] = useState(init.customer ?? "");
  const [kind, setKind] = useState<TicketRequest["kind"]>(init.kind ?? "Incident");
  const [title, setTitle] = useState(init.title ?? "");
  const [description, setDescription] = useState(init.description ?? "");
  const [priority, setPriority] = useState<TicketRequest["priority"]>(3);
  const [devices, setDevices] = useState<string[]>(init.devices ?? []);
  const [start, setStart] = useState(() => toLocalInput(quarterFromNow(24 * 60)));
  const [minutes, setMinutes] = useState(60);
  const [risk, setRisk] = useState<"Low" | "Medium" | "High">("Low");
  const [template, setTemplate] = useState(init.template ?? "none");
  const [rollback, setRollback] = useState("");
  const { data } = useCustomer(customer);
  const action = useAction((req: TicketRequest) => api.createTicket(req));
  const change = kind === "Change";

  const submit = () => {
    const startMs = fromLocalInput(start);
    action.mutate(
      {
        customer, kind, title, description, priority, devices,
        ...(change ? { plannedStart: new Date(startMs).toISOString(), plannedEnd: new Date(startMs + minutes * 60_000).toISOString(), risk, template: template === "none" ? null : template, rollback } : {}),
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={wide}>
        <DialogHeader>
          <DialogTitle>{change ? "Open a GLPI change" : "Open a GLPI ticket"}</DialogTitle>
          <DialogDescription>
            Created in the customer's GLPI entity with you as the requester{change ? ". It goes through approval in GLPI before anything runs." : ", under the customer's service level."}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <ToggleGroup type="single" variant="outline" value={kind} onValueChange={(v) => v && setKind(v as TicketRequest["kind"])} className="w-full">
            <ToggleGroupItem value="Incident" className="flex-1">Incident</ToggleGroupItem>
            <ToggleGroupItem value="Request" className="flex-1">Request</ToggleGroupItem>
            <ToggleGroupItem value="Change" className="flex-1">Change</ToggleGroupItem>
          </ToggleGroup>
          {!init.customer && (
            <Field label="Customer">
              <CustomerSelect value={customer} onChange={(c) => { setCustomer(c); setDevices([]); }} />
            </Field>
          )}
          <Field label="Title" htmlFor="t-title">
            <Input id="t-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder={change ? "What will change, on which device" : "What is wrong, where"} />
          </Field>
          <Field label="Description" htmlFor="t-desc">
            <Textarea id="t-desc" rows={4} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={change ? "The steps, and how you will check the result" : "What happens, since when, who reported it"} />
          </Field>
          {!change && (
            <Field label="Priority">
              <Select value={String(priority)} onValueChange={(v) => setPriority(Number(v) as TicketRequest["priority"])}>
                <SelectTrigger className="w-full sm:w-56"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(["Very low", "Low", "Medium", "High", "Very high"] as const).map((label, i) => (
                    <SelectItem key={label} value={String(i + 1)}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
          {data && (
            <Field label={change ? "Devices it changes" : "Devices (optional)"}>
              <DevicePicker devices={data.devices} value={devices} onChange={setDevices} activeOnly={false} />
            </Field>
          )}
          {change && (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={`Planned start (${zoneLabel()})`} htmlFor="t-start">
                  <Input id="t-start" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
                </Field>
                <Field label="Duration">
                  <Select value={String(minutes)} onValueChange={(v) => setMinutes(Number(v))}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {[15, 30, 60, 90, 120, 240, 480].map((m) => (
                        <SelectItem key={m} value={String(m)}>{duration(m * 60)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Risk">
                  <Select value={risk} onValueChange={(v) => setRisk(v as typeof risk)}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {(["Low", "Medium", "High"] as const).map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="Run automatically when accepted">
                  <Select value={template} onValueChange={setTemplate}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Nothing, the engineer does the work</SelectItem>
                      {CHANGE_TEMPLATES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </Field>
              </div>
              <Field label="Back-out plan" htmlFor="t-rollback">
                <Textarea id="t-rollback" rows={2} value={rollback} onChange={(e) => setRollback(e.target.value)} placeholder="How you undo it if the check fails" />
              </Field>
              {template !== "none" && (
                <Summary>
                  When the change is accepted in GLPI, msp-glue runs <b>{template}</b> in the customer's Semaphore project{devices.length ? ` on ${devices.join(", ")}` : ""} and adds the result to the change.
                </Summary>
              )}
            </>
          )}
        </div>
        <ErrorNote error={action.error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!customer || title.trim().length < 5 || action.isPending} onClick={submit}>
            {action.isPending ? "Creating" : change ? "Create change" : "Create ticket"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------- maintenance
export interface MaintenanceInit {
  customer?: string;
  devices?: string[];
  changeId?: number;
  name?: string;
}

export function MaintenanceDialog({ init, onClose }: { init: MaintenanceInit; onClose: () => void }) {
  const [customer, setCustomer] = useState(init.customer ?? "");
  const [scope, setScope] = useState<"devices" | "all">("devices");
  const [devices, setDevices] = useState<string[]>(init.devices ?? []);
  const [name, setName] = useState(init.name ?? "");
  const [startMode, setStartMode] = useState<"now" | "later">("now");
  const [start, setStart] = useState(() => toLocalInput(quarterFromNow(60)));
  const [minutes, setMinutes] = useState(60);
  const [collect, setCollect] = useState(true);
  const [changeId, setChangeId] = useState<number | null>(init.changeId ?? null);
  const { data } = useCustomer(customer);
  const action = useAction((req: MaintenanceRequest) => api.createMaintenance(req));
  const openChanges = useMemo(
    () => (data?.changes ?? []).filter((c) => !["Applied", "Closed", "Review", "Refused", "Cancelled"].includes(c.status)),
    [data],
  );
  const regulated = data?.summary.regulated ?? false;
  const startMs = startMode === "now" ? Date.now() : fromLocalInput(start);
  const endMs = startMs + minutes * 60_000;

  useEffect(() => {
    if (!name && changeId) {
      const ch = openChanges.find((c) => c.id === changeId);
      if (ch) setName(`Change #${ch.id}: ${ch.name}`.slice(0, 120));
    }
  }, [changeId, openChanges, name]);

  const submit = () =>
    action.mutate(
      { customer, devices: scope === "all" ? null : devices, name, start: new Date(startMs).toISOString(), minutes, collectData: collect, changeId },
      { onSuccess: onClose },
    );

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={wide}>
        <DialogHeader>
          <DialogTitle>Plan maintenance in Zabbix</DialogTitle>
          <DialogDescription>
            During the window Zabbix sends no alerts and opens no tickets for the chosen devices. Their problems stay visible here, marked as in maintenance.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          {!init.customer && (
            <Field label="Customer">
              <CustomerSelect value={customer} onChange={(c) => { setCustomer(c); setDevices([]); setChangeId(null); }} />
            </Field>
          )}
          <Field label="Name" htmlFor="m-name" hint="Zabbix shows it on every problem the window hides.">
            <Input id="m-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="Store power works, firmware upgrade, ..." />
          </Field>
          {data && (
            <Field label="Devices">
              <RadioGroup value={scope} onValueChange={(v) => setScope(v as "devices" | "all")} className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-[13px]"><RadioGroupItem value="devices" /> Chosen devices</label>
                <label className="flex items-center gap-2 text-[13px]"><RadioGroupItem value="all" /> Every device of {data.summary.name}</label>
              </RadioGroup>
              {scope === "devices" && <DevicePicker devices={data.devices.filter((d) => d.host)} value={devices} onChange={setDevices} />}
            </Field>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Starts">
              <RadioGroup value={startMode} onValueChange={(v) => setStartMode(v as "now" | "later")} className="flex gap-4 pt-1">
                <label className="flex items-center gap-2 text-[13px]"><RadioGroupItem value="now" /> Now</label>
                <label className="flex items-center gap-2 text-[13px]"><RadioGroupItem value="later" /> At</label>
              </RadioGroup>
              {startMode === "later" && <Input type="datetime-local" aria-label={`Start (${zoneLabel()})`} value={start} onChange={(e) => setStart(e.target.value)} />}
            </Field>
            <Field label="Lasts">
              <Select value={String(minutes)} onValueChange={(v) => setMinutes(Number(v))}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {[30, 60, 120, 240, 480, 720, 1440].map((m) => <SelectItem key={m} value={String(m)}>{duration(m * 60)}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <label className="flex items-start gap-3 rounded-md border px-3 py-2.5 text-[13px]">
            <Switch checked={collect} onCheckedChange={setCollect} className="mt-0.5" />
            <span>
              <span className="font-medium">Keep collecting data</span>
              <span className="block text-muted-foreground">On: graphs keep their values through the window. Off: Zabbix stops polling the devices.</span>
            </span>
          </label>
          {data && (
            <Field label={regulated ? "GLPI change (required for this customer)" : "GLPI change (optional)"}>
              <Select value={changeId === null ? "none" : String(changeId)} onValueChange={(v) => setChangeId(v === "none" ? null : Number(v))}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {!regulated && <SelectItem value="none">No change</SelectItem>}
                  {openChanges.map((c) => <SelectItem key={c.id} value={String(c.id)}>Change #{c.id}: {c.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
          )}
          {customer && name.trim().length >= 5 && (scope === "all" || devices.length > 0) && (
            <Summary>
              From {startMode === "now" ? "now" : when(startMs)} to {timeOnly(endMs)} {zoneLabel()} ({duration(minutes * 60)}), on {scope === "all" ? "every device of the customer" : plural(devices.length, "device", "devices")}.
              {changeId ? ` Linked to change #${changeId}.` : ""} Recorded in the audit trail under your name.
            </Summary>
          )}
        </div>
        <ErrorNote error={action.error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!customer || name.trim().length < 5 || (scope === "devices" && !devices.length) || action.isPending} onClick={submit}>
            {action.isPending ? "Saving" : "Set maintenance"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------------ device
const PLATFORMS: { name: string; roles: DeviceRole[]; gc: boolean; note: string }[] = [
  { name: "Cisco IOS", roles: ["switch", "router"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "Cisco IOS-XE", roles: ["router", "switch"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "Cisco NX-OS", roles: ["switch"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "Cisco ASA", roles: ["firewall"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "FortiOS", roles: ["firewall"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "Junos", roles: ["router", "switch", "firewall"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "Arista EOS", roles: ["switch"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "AOS-CX", roles: ["switch"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "Huawei VRP", roles: ["router", "switch"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "RouterOS", roles: ["router"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "TP-Link JetStream", roles: ["switch"], gc: true, note: "SNMP monitoring, backups and checks" },
  { name: "Linux", roles: ["server"], gc: false, note: "Zabbix agent: install it on the server, the platform registers it" },
  { name: "Windows", roles: ["server"], gc: false, note: "Zabbix agent: install it on the server, the platform registers it" },
];

export function DeviceDialog({ init, onClose }: { init: { customer?: string }; onClose: () => void }) {
  const [customer, setCustomer] = useState(init.customer ?? "");
  const [site, setSite] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<DeviceRole>("switch");
  const [platform, setPlatform] = useState("Cisco IOS");
  const [model, setModel] = useState("");
  const [serial, setSerial] = useState("");
  const [status, setStatus] = useState<"Planned" | "Active">("Planned");
  const [ip, setIp] = useState("");
  const [monitoring, setMonitoring] = useState(true);
  const { data } = useCustomer(customer);
  const action = useAction((req: DeviceRequest) => api.addDevice(req));
  const platforms = PLATFORMS.filter((p) => p.roles.includes(role));
  const chosen = PLATFORMS.find((p) => p.name === platform);

  useEffect(() => {
    if (!platforms.some((p) => p.name === platform)) setPlatform(platforms[0]?.name ?? "");
  }, [role, platform, platforms]);
  useEffect(() => {
    if (data && !site) setSite(data.sites[0]?.id ?? "");
  }, [data, site]);

  const submit = () =>
    action.mutate({ customer, site, name, role, platform, model, status, ip, serial, monitoring }, { onSuccess: onClose });

  const gc = status === "Active" && Boolean(ip) && Boolean(chosen?.gc);
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={wide}>
        <DialogHeader>
          <DialogTitle>Add a device</DialogTitle>
          <DialogDescription>
            The device is created in Nautobot, the source of truth. msp-glue then adds it to Zabbix, GLPI and Golden Config, and to the customer's Semaphore inventory.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          {!init.customer && (
            <Field label="Customer">
              <CustomerSelect value={customer} onChange={(c) => { setCustomer(c); setSite(""); }} />
            </Field>
          )}
          {data && (
            <Field label="Site">
              <Select value={site || undefined} onValueChange={setSite}>
                <SelectTrigger className="w-full"><SelectValue placeholder="Choose the site" /></SelectTrigger>
                <SelectContent>
                  {data.sites.map((s) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" htmlFor="d-name" hint={site ? `For example ${site}-sw03` : undefined}>
              <Input id="d-name" value={name} onChange={(e) => setName(e.target.value)} className="font-mono" autoComplete="off" spellCheck={false} />
            </Field>
            <Field label="Role">
              <Select value={role} onValueChange={(v) => setRole(v as DeviceRole)}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(["router", "switch", "firewall", "server"] as const).map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Platform" hint={chosen?.note}>
              <Select value={platform} onValueChange={setPlatform}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {platforms.map((p) => <SelectItem key={p.name} value={p.name}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Model" htmlFor="d-model">
              <Input id="d-model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="C9200L-24T-4G" />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Status">
              <RadioGroup value={status} onValueChange={(v) => setStatus(v as "Planned" | "Active")} className="flex gap-4 pt-1">
                <label className="flex items-center gap-2 text-[13px]"><RadioGroupItem value="Planned" /> Planned</label>
                <label className="flex items-center gap-2 text-[13px]"><RadioGroupItem value="Active" /> Active</label>
              </RadioGroup>
            </Field>
            <Field label="Serial number" htmlFor="d-serial">
              <Input id="d-serial" value={serial} onChange={(e) => setSerial(e.target.value)} className="font-mono" autoComplete="off" />
            </Field>
          </div>
          <Field label={status === "Active" ? "Management address" : "Management address (optional)"} htmlFor="d-ip">
            <Input id="d-ip" value={ip} onChange={(e) => setIp(e.target.value)} inputMode="decimal" className="font-mono" placeholder="10.23.0.12" autoComplete="off" />
          </Field>
          <label className="flex items-start gap-3 rounded-md border px-3 py-2.5 text-[13px]">
            <Switch checked={monitoring} onCheckedChange={setMonitoring} className="mt-0.5" />
            <span>
              <span className="font-medium">Monitor it in Zabbix</span>
              <span className="block text-muted-foreground">Off: msp-glue still adds it to GLPI and Golden Config, but not to Zabbix (for devices the customer monitors).</span>
            </span>
          </label>
          {customer && name && (
            <Summary>
              {status === "Planned"
                ? "Nautobot keeps it as Planned. Nothing is provisioned until you set it to Active with its address."
                : `Within a minute: ${monitoring ? "a Zabbix host with the " + platform + " template, " : ""}a GLPI asset${gc ? ", nightly backups and checks in Golden Config" : ""}, and an entry in the customer's Semaphore inventory.`}
            </Summary>
          )}
        </div>
        <ErrorNote error={action.error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!customer || !site || !name || action.isPending} onClick={submit}>
            {action.isPending ? "Adding" : "Add device"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ re-sync and configuration check
export function ConfirmDialog({
  kind,
  customer,
  name,
  onClose,
}: {
  kind: "resync" | "config";
  customer: string;
  name: string;
  onClose: () => void;
}) {
  const action = useAction((code: string) => (kind === "resync" ? api.resync(code) : api.configCheck(code)));
  const text =
    kind === "resync"
      ? {
          title: `Re-sync ${name}?`,
          body: "msp-glue checks and repairs the customer's set-up in Zabbix, GLPI, Semaphore, Forgejo and Golden Config, then pushes its devices to monitoring and GLPI again. Use it after a change in Nautobot or a set-up error.",
          button: "Re-sync customer",
        }
      : {
          title: `Run a configuration check for ${name}?`,
          body: "Golden Config logs in to each network device, saves a backup to Forgejo and compares it with the intended configuration, now instead of tonight. Drift tickets in GLPI are updated when it finishes.",
          button: "Run configuration check",
        };
  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{text.title}</AlertDialogTitle>
          <AlertDialogDescription>{text.body}</AlertDialogDescription>
        </AlertDialogHeader>
        <ErrorNote error={action.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={action.isPending}
            onClick={(e) => {
              e.preventDefault();
              action.mutate(customer, { onSuccess: onClose });
            }}
          >
            {action.isPending ? "Starting" : text.button}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function RoleHint({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("text-[12.5px] text-muted-foreground", className)}>{children}</p>;
}

export function DeviceLink({ customer, name, className }: { customer: string; name: string; className?: string }) {
  return (
    <Link to={`/customers/${customer}/devices/${name}`} className={cn("font-medium hover:underline", className)}>
      {name}
    </Link>
  );
}
