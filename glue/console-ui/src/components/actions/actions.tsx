/* One place that opens the action dialogs, so a page, the command menu and the Investigate panel all
   start the same dialog the same way. Roles are checked before a dialog opens and again on the server. */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";
import type { Problem } from "@/lib/api";
import { useMe } from "@/lib/queries";
import {
  AckDialog,
  ConfirmDialog,
  DeviceDialog,
  MaintenanceDialog,
  PlaybookDialog,
  TicketDialog,
  type MaintenanceInit,
  type PlaybookInit,
  type TicketInit,
} from "./dialogs";
import { InvestigateSheet } from "./investigate";

type Open =
  | { kind: "ack"; problem: Problem }
  | { kind: "playbook"; init: PlaybookInit }
  | { kind: "ticket"; init: TicketInit }
  | { kind: "maintenance"; init: MaintenanceInit }
  | { kind: "device"; init: { customer?: string } }
  | { kind: "resync" | "config"; customer: string; name: string };

export interface Actions {
  acknowledge: (problem: Problem) => void;
  investigate: (problem: Problem) => void;
  playbook: (init: PlaybookInit) => void;
  ticket: (init?: TicketInit) => void;
  maintenance: (init?: MaintenanceInit) => void;
  addDevice: (init?: { customer?: string }) => void;
  resync: (customer: string, name: string) => void;
  configCheck: (customer: string, name: string) => void;
  canAct: boolean;
  canRun: boolean;
  /** engineer, and the console has a model endpoint for Investigate */
  canInvestigate: boolean;
}

const ActionsContext = createContext<Actions | null>(null);

export function ActionsProvider({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  const [open, setOpen] = useState<Open | null>(null);
  const [investigating, setInvestigating] = useState<Problem | null>(null);
  const canAct = Boolean(me?.canAct);
  const canRun = Boolean(me?.canRun);
  const canInvestigate = canAct && Boolean(me?.investigate);

  const engineer = useCallback(
    (what: string, then: () => void) => {
      if (!canAct) toast.error(`${what} needs the engineer role.`);
      else then();
    },
    [canAct],
  );

  const value = useMemo<Actions>(
    () => ({
      canAct,
      canRun,
      canInvestigate,
      acknowledge: (problem) => engineer("Acknowledging problems", () => setOpen({ kind: "ack", problem })),
      investigate: (problem) =>
        engineer("Investigating problems", () =>
          canInvestigate
            ? setInvestigating(problem)
            : toast.error("Investigate is switched off: an administrator sets a model endpoint (CONSOLE_AI_URL) for it."),
        ),
      playbook: (init) => (canRun ? setOpen({ kind: "playbook", init }) : toast.error("Starting playbooks needs the automation role.")),
      ticket: (init = {}) => engineer("Opening tickets", () => setOpen({ kind: "ticket", init })),
      maintenance: (init = {}) => engineer("Planning maintenance", () => setOpen({ kind: "maintenance", init })),
      addDevice: (init = {}) => engineer("Adding devices", () => setOpen({ kind: "device", init })),
      resync: (customer, name) => engineer("Re-syncing customers", () => setOpen({ kind: "resync", customer, name })),
      configCheck: (customer, name) => engineer("Starting a configuration check", () => setOpen({ kind: "config", customer, name })),
    }),
    [canAct, canRun, canInvestigate, engineer],
  );

  const close = () => setOpen(null);
  return (
    <ActionsContext.Provider value={value}>
      {children}
      {investigating && (
        <InvestigateSheet
          key={investigating.eventid}
          problem={investigating}
          onClose={() => setInvestigating(null)}
          onPlaybook={(init) => value.playbook(init)}
          onTicket={(init) => value.ticket(init)}
        />
      )}
      {open?.kind === "ack" && <AckDialog problem={open.problem} onClose={close} />}
      {open?.kind === "playbook" && <PlaybookDialog init={open.init} onClose={close} />}
      {open?.kind === "ticket" && <TicketDialog init={open.init} onClose={close} />}
      {open?.kind === "maintenance" && <MaintenanceDialog init={open.init} onClose={close} />}
      {open?.kind === "device" && <DeviceDialog init={open.init} onClose={close} />}
      {(open?.kind === "resync" || open?.kind === "config") && (
        <ConfirmDialog kind={open.kind} customer={open.customer} name={open.name} onClose={close} />
      )}
    </ActionsContext.Provider>
  );
}

export function useActions(): Actions {
  const value = useContext(ActionsContext);
  if (!value) throw new Error("useActions outside ActionsProvider");
  return value;
}
