/* Investigate: the console gathers evidence from every tool (read-only, with its own accounts), a model
   drafts the likely cause and next steps, and the engineer checks the draft before it goes to the ticket. */
import { useMutation, useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, Copy, LoaderCircle, OctagonX, Play, ScanSearch, Ticket } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, type Investigation, type InvestigationStep, type Problem } from "@/lib/api";
import { since } from "@/lib/format";
import { errorText, useAction, useMe } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { Pill, SeverityTag } from "@/components/status";
import { ToolLink } from "@/components/tool-link";
import { ErrorNote } from "./form";
import type { PlaybookInit, TicketInit } from "./dialogs";

function StepIcon({ status }: { status: InvestigationStep["status"] }) {
  if (status === "done") return <CircleCheck className="size-4 text-ok" aria-label="done" />;
  if (status === "running") return <LoaderCircle className="size-4 animate-spin text-info" aria-label="reading" />;
  if (status === "failed") return <OctagonX className="size-4 text-bad" aria-label="failed" />;
  return <CircleDashed className="size-4 text-muted-foreground" aria-label="waiting" />;
}

export function InvestigateSheet({
  problem,
  onClose,
  onPlaybook,
  onTicket,
}: {
  problem: Problem;
  onClose: () => void;
  onPlaybook: (init: PlaybookInit) => void;
  onTicket: (init: TicketInit) => void;
}) {
  const { data: me } = useMe();
  const start = useMutation({ mutationFn: () => api.startInvestigation(problem.eventid) });
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    start.mutate();
  }, [start]);

  const id = start.data?.id;
  const poll = useQuery({
    queryKey: ["investigation", id],
    queryFn: () => api.investigation(id!),
    enabled: Boolean(id),
    refetchInterval: (q) => (q.state.data?.status === "done" || q.state.data?.status === "failed" ? false : 650),
  });
  const inv: Investigation | undefined = poll.data ?? start.data;
  const [note, setNote] = useState("");
  useEffect(() => {
    if (inv?.draftNote) setNote((current) => current || inv.draftNote!);
  }, [inv?.draftNote]);
  const post = useAction((text: string) => api.postNote({ itemType: "Ticket", itemId: problem.ticketId!, text }));
  const done = inv?.status === "done";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(note);
      toast.success("Note copied.");
    } catch {
      toast.error("The browser did not allow copying here. Select the text and copy it.");
    }
  };

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <SheetHeader className="border-b px-5 pb-4 pt-5">
          <SheetTitle className="flex items-center gap-2 text-[17px]">
            <ScanSearch className="size-5 text-primary" /> Investigate
          </SheetTitle>
          <SheetDescription className="text-[13px]">
            Read-only: the console reads Zabbix, Nautobot, GLPI, Forgejo and Semaphore with its own accounts and changes nothing. A model drafts the cause; you check it
            before it goes to the ticket.
          </SheetDescription>
          <div className="mt-3 rounded-md border bg-muted/50 px-3 py-2.5 text-[13px]">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <SeverityTag severity={problem.severity} />
              <span className="font-semibold">{problem.name}</span>
            </div>
            <div className="mt-1 text-muted-foreground">
              {problem.device} at {problem.site}, {problem.customerName}. Open for {since(problem.started)}.
            </div>
          </div>
        </SheetHeader>

        <div className="space-y-5 px-5 py-5">
          {inv?.sample && (
            <p className="rounded-md border border-preview-border bg-preview px-3 py-2 text-[12.5px] text-preview-foreground">
              Sample answer for the preview: no model was called. In the kit the console sends this evidence to the model endpoint set in the platform settings (Ollama, vLLM,
              Netra or any OpenAI-compatible server) and nothing else.
            </p>
          )}
          <ErrorNote error={start.error ?? poll.error} />

          <section aria-label="Evidence">
            <h3 className="mb-2 text-[13.5px] font-semibold">What the console looked at</h3>
            <ol className="space-y-2">
              {(inv?.steps ?? []).map((s) => (
                <li key={s.id} className={cn("rounded-md border px-3 py-2", s.status === "waiting" && "opacity-60")}>
                  <div className="flex items-start gap-2.5">
                    <span className="mt-0.5">
                      <StepIcon status={s.status} />
                    </span>
                    <div className="min-w-0 flex-1 text-[13px]">
                      <div className="flex flex-wrap items-center gap-x-2">
                        <span className="rounded bg-secondary px-1.5 py-px text-[11.5px] font-medium text-secondary-foreground">{s.tool}</span>
                        <span>{s.title}</span>
                      </div>
                      {s.finding && <p className="mt-1 text-muted-foreground">{s.finding}</p>}
                      {s.finding && s.link && (
                        <ToolLink tool={s.tool} href={s.link} icon className="mt-1 text-[12.5px]">
                          Open in {s.tool}
                        </ToolLink>
                      )}
                    </div>
                  </div>
                </li>
              ))}
              {!inv && <li className="text-[13px] text-muted-foreground">Starting…</li>}
            </ol>
          </section>

          {done && inv && (
            <>
              <section aria-label="Likely cause" className="rounded-md border-l-[3px] border-l-primary bg-accent/50 px-4 py-3">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <h3 className="text-[13.5px] font-semibold">Likely cause</h3>
                  <Pill tone="neutral" icon={null}>draft</Pill>
                  {inv.confidence && <Pill tone="neutral" icon={null}>{inv.confidence} confidence</Pill>}
                </div>
                <p className="text-[13.5px] leading-relaxed">{inv.cause}</p>
              </section>

              <section aria-label="Next steps">
                <h3 className="mb-2 text-[13.5px] font-semibold">Next steps</h3>
                <ul className="space-y-2">
                  {inv.nextSteps.map((n, i) => (
                    <li key={i} className="flex flex-col gap-2 rounded-md border px-3 py-2 text-[13px] sm:flex-row sm:items-center sm:justify-between">
                      <span>{n.text}</span>
                      {n.playbook && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="shrink-0"
                          disabled={!me?.canRun}
                          title={me?.canRun ? undefined : "Starting playbooks needs the automation role"}
                          onClick={() =>
                            onPlaybook({
                              customer: problem.customer,
                              devices: n.playbook!.devices,
                              template: n.playbook!.template,
                              item: problem.ticketId ? { type: "Ticket", id: problem.ticketId } : undefined,
                              reason: n.text,
                            })
                          }
                        >
                          <Play /> {n.playbook.template}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              </section>

              <section aria-label="Note for the ticket">
                <h3 className="mb-1 text-[13.5px] font-semibold">{problem.ticketId ? `Note for ticket #${problem.ticketId}` : "Note"}</h3>
                <p className="mb-2 text-[12.5px] text-muted-foreground">
                  Edit it as you need. It goes to GLPI as a private follow-up under your name, marked as written with the assistant.
                </p>
                <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={9} className="font-mono text-[12.5px]" />
                <ErrorNote error={post.error ? errorText(post.error) : null} />
                <div className="mt-3 flex flex-wrap gap-2">
                  {problem.ticketId ? (
                    <Button disabled={post.isPending || note.trim().length < 10} onClick={() => post.mutate(note, { onSuccess: onClose })}>
                      <Ticket /> {post.isPending ? "Posting" : `Post to ticket #${problem.ticketId}`}
                    </Button>
                  ) : (
                    <Button
                      onClick={() =>
                        onTicket({
                          customer: problem.customer,
                          kind: "Incident",
                          devices: [problem.device],
                          title: `${problem.device}: ${problem.name}`,
                          description: note,
                        })
                      }
                    >
                      <Ticket /> Open a ticket with this note
                    </Button>
                  )}
                  <Button variant="outline" onClick={copy}>
                    <Copy /> Copy
                  </Button>
                </div>
              </section>
              <p className="text-[12px] text-muted-foreground">
                Model: {inv.model.name} ({inv.model.endpoint}). Started {since(inv.started)} ago.
              </p>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
