/* The colour of a status, and its words. Colour is never the only signal: pills carry an icon and a word. */
import type { HostState, Outcome, Severity, Ticket } from "@/lib/api";

export type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

export const SEVERITY_NAMES = ["Not classified", "Information", "Warning", "Average", "High", "Disaster"] as const;
export const severityName = (s: Severity) => SEVERITY_NAMES[s];

export function outcomeTone(value: string): Tone {
  const v = value.toLowerCase();
  if (["success", "done", "ok", "provisioned", "up", "met", "applied", "accepted", "compliant"].includes(v)) return "ok";
  if (["error", "failed", "failure", "down", "refused", "breached", "cancelled"].includes(v)) return "bad";
  if (["stopped", "unknown", "pending", "errors", "drift", "approval", "evaluation", "waiting for approval"].includes(v)) return "warn";
  if (["running", "waiting", "started", "queued", "active", "in progress", "upcoming", "new"].includes(v)) return "info";
  return "neutral";
}

export function hostTone(state: HostState | "planned" | "external" | undefined): Tone {
  if (state === "up") return "ok";
  if (state === "down") return "bad";
  if (state === "unknown") return "warn";
  return "neutral";
}

export function severityTone(s: Severity | null | undefined): Tone {
  if (s === null || s === undefined) return "ok";
  if (s >= 4) return "bad";
  if (s >= 2) return "warn";
  return "info";
}

export const PRIORITY_NAMES: Record<Ticket["priority"], string> = {
  1: "Very low", 2: "Low", 3: "Medium", 4: "High", 5: "Very high", 6: "Major",
};

export function outcomeLabel(outcome: Outcome | string): string {
  return ({ errors: "finished with errors", success: "success", done: "done", started: "started", queued: "queued" } as Record<string, string>)[outcome] ?? outcome;
}
