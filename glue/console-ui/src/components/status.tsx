import {
  CircleCheck,
  CircleDashed,
  CirclePause,
  CircleQuestionMark,
  CircleSlash,
  Info,
  LoaderCircle,
  OctagonX,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import type { HostState, Severity } from "@/lib/api";
import { cn } from "@/lib/utils";
import { hostTone, outcomeLabel, outcomeTone, severityName, type Tone } from "@/lib/status";

const TONE: Record<Tone, string> = {
  ok: "bg-ok-bg text-ok",
  warn: "bg-warn-bg text-warn",
  bad: "bg-bad-bg text-bad",
  info: "bg-info-bg text-info",
  neutral: "bg-neutral-bg text-neutral",
};

const TONE_ICON: Record<Tone, LucideIcon> = {
  ok: CircleCheck,
  warn: TriangleAlert,
  bad: OctagonX,
  info: Info,
  neutral: CircleDashed,
};

export function toneText(tone: Tone) {
  return { ok: "text-ok", warn: "text-warn", bad: "text-bad", info: "text-info", neutral: "text-muted-foreground" }[tone];
}

export function Pill({
  tone,
  icon,
  children,
  title,
  className,
  spin,
}: {
  tone: Tone;
  icon?: LucideIcon | null;
  children: ReactNode;
  title?: string;
  className?: string;
  spin?: boolean;
}) {
  const Icon = icon === null ? null : (icon ?? TONE_ICON[tone]);
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-[22px] max-w-full items-center gap-1 whitespace-nowrap rounded-full px-2 text-[12px] font-medium leading-none",
        TONE[tone],
        className,
      )}
    >
      {Icon && <Icon aria-hidden className={cn("size-3.5 shrink-0", spin && "animate-spin")} />}
      <span className="truncate">{children}</span>
    </span>
  );
}

export function OutcomePill({ outcome, title }: { outcome: string; title?: string }) {
  const tone = outcomeTone(outcome);
  const running = ["running", "started", "queued", "waiting"].includes(outcome);
  return (
    <Pill tone={tone} title={title} icon={running ? LoaderCircle : undefined} spin={running && outcome !== "waiting"}>
      {outcomeLabel(outcome)}
    </Pill>
  );
}

const HOST_ICON: Record<string, LucideIcon> = {
  up: CircleCheck,
  down: OctagonX,
  unknown: CircleQuestionMark,
  disabled: CircleSlash,
  planned: CircleDashed,
};

export function HostPill({ state, maintenance, title }: { state: HostState | "planned"; maintenance?: boolean; title?: string }) {
  if (maintenance) {
    return (
      <Pill tone="info" icon={CirclePause} title={title ?? "In a Zabbix maintenance window: no alerts are sent"}>
        {state === "down" ? "down, in maintenance" : "in maintenance"}
      </Pill>
    );
  }
  return (
    <Pill tone={hostTone(state)} icon={HOST_ICON[state]} title={title}>
      {state}
    </Pill>
  );
}

/** Zabbix severity: the colour engineers know from Zabbix, always with its name. */
export function SeverityTag({ severity, className, short }: { severity: Severity; className?: string; short?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] font-medium", className)}>
      <span aria-hidden className="size-2.5 shrink-0 rounded-[3px]" style={{ background: `var(--sev-${severity})` }} />
      {short && severity === 0 ? "None" : severityName(severity)}
    </span>
  );
}

/** A thin bar on the left edge of a problem row, in the severity's colour. */
export function SeverityEdge({ severity, muted }: { severity: Severity; muted?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn("absolute inset-y-1 left-0 w-1 rounded-full", muted && "opacity-40")}
      style={{ background: `var(--sev-${severity})` }}
    />
  );
}

export function StateDot({ tone, className }: { tone: Tone; className?: string }) {
  const color = { ok: "bg-[var(--status-good)]", warn: "bg-[var(--status-warning)]", bad: "bg-[var(--status-critical)]", info: "bg-[var(--series-1)]", neutral: "bg-[var(--chart-muted)]" }[tone];
  return <span aria-hidden className={cn("inline-block size-2 shrink-0 rounded-full", color, className)} />;
}
