/* Pieces the action dialogs share: labelled fields, the device picker, the ticket-or-change picker,
   the "what will happen" summary and the error line. */
import { OctagonX } from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";
import type { Change, Device, Ticket } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { HostPill } from "@/components/status";

export function Field({ label, hint, children, htmlFor, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; htmlFor?: string; className?: string }) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <Label htmlFor={htmlFor} className="text-[13px] font-medium">
        {label}
      </Label>
      {children}
      {hint && <p className="text-[12.5px] leading-snug text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-[13px] text-bad">
      <OctagonX className="mt-0.5 size-4 shrink-0" />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  );
}

/** What pressing the button will do, in which tool, and what lands in the ticket. */
export function Summary({ children }: { children: ReactNode }) {
  return <div className="rounded-md border bg-muted/60 px-3 py-2.5 text-[13px] leading-relaxed">{children}</div>;
}

export function DevicePicker({
  devices,
  value,
  onChange,
  activeOnly = true,
}: {
  devices: Device[];
  value: string[];
  onChange: (next: string[]) => void;
  activeOnly?: boolean;
}) {
  const [filter, setFilter] = useState("");
  const id = useId();
  const list = useMemo(
    () => devices.filter((d) => (!activeOnly || d.status === "Active") && (!filter || `${d.name} ${d.site} ${d.role}`.toLowerCase().includes(filter.toLowerCase()))),
    [devices, filter, activeOnly],
  );
  const toggle = (name: string, on: boolean) => onChange(on ? [...new Set([...value, name])].sort() : value.filter((x) => x !== name));
  return (
    <div className="rounded-md border">
      {devices.length > 6 && (
        <div className="border-b p-2">
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter devices" aria-label="Filter devices" className="h-8" />
        </div>
      )}
      <ul className="max-h-44 overflow-y-auto p-1">
        {list.map((d) => {
          const box = `${id}-${d.name}`;
          return (
            <li key={d.name}>
              <label htmlFor={box} className="flex cursor-pointer items-center gap-2.5 rounded px-2 py-1.5 text-[13px] hover:bg-accent">
                <Checkbox id={box} checked={value.includes(d.name)} onCheckedChange={(on) => toggle(d.name, on === true)} />
                <span className="font-medium">{d.name}</span>
                <span className="truncate text-muted-foreground">{d.site}</span>
                <span className="ml-auto">{d.host ? <HostPill state={d.host.state} maintenance={d.host.maintenance} /> : null}</span>
              </label>
            </li>
          );
        })}
        {!list.length && <li className="px-2 py-3 text-[13px] text-muted-foreground">No device matches.</li>}
      </ul>
      <div className="flex items-center justify-between border-t px-3 py-1.5 text-[12.5px] text-muted-foreground">
        <span>{value.length ? `${value.length} chosen: ${value.join(", ")}` : "None chosen"}</span>
        {value.length > 0 && (
          <button type="button" className="hover:text-foreground hover:underline" onClick={() => onChange([])}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

export interface ItemChoice {
  type: "Ticket" | "Change";
  id: number;
}

export function ItemPicker({
  tickets,
  changes,
  value,
  onChange,
  changesOnly,
}: {
  tickets: Ticket[];
  changes: Change[];
  value: ItemChoice | null;
  onChange: (next: ItemChoice | null) => void;
  changesOnly?: boolean;
}) {
  const open = changes.filter((c) => !["Applied", "Closed", "Review", "Refused", "Cancelled"].includes(c.status));
  const options: { type: "Ticket" | "Change"; id: number; text: string; note: string; disabled: boolean }[] = [
    ...open.map((c) => ({
      type: "Change" as const, id: c.id, text: c.name, note: c.status,
      disabled: Boolean(changesOnly) && c.status !== "Accepted",
    })),
    ...(changesOnly ? [] : tickets.map((t) => ({ type: "Ticket" as const, id: t.id, text: t.name, note: t.status, disabled: false }))),
  ];
  const group = useId();
  return (
    <div className="rounded-md border">
      <ul className="max-h-44 overflow-y-auto p-1" role="radiogroup">
        {options.map((o) => {
          const checked = value?.type === o.type && value.id === o.id;
          return (
            <li key={`${o.type}${o.id}`}>
              <label
                className={cn(
                  "flex cursor-pointer items-start gap-2.5 rounded px-2 py-1.5 text-[13px] hover:bg-accent",
                  o.disabled && "cursor-not-allowed opacity-55 hover:bg-transparent",
                )}
              >
                <input
                  type="radio"
                  name={group}
                  className="mt-0.5 accent-[var(--primary)]"
                  checked={checked}
                  disabled={o.disabled}
                  onChange={() => onChange({ type: o.type, id: o.id })}
                />
                <span className="min-w-0">
                  <span className="font-medium">
                    {o.type} #{o.id}
                  </span>{" "}
                  <span>{o.text}</span>
                  <span className="block text-[12px] text-muted-foreground">
                    {o.note}
                    {o.disabled ? ": needs status Accepted" : ""}
                  </span>
                </span>
              </label>
            </li>
          );
        })}
        {!options.length && (
          <li className="px-2 py-3 text-[13px] text-muted-foreground">
            {changesOnly ? "No open change for this customer. Open a change first." : "No open ticket or change for this customer. Open one first."}
          </li>
        )}
      </ul>
    </div>
  );
}
