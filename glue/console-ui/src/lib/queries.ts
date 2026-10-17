/* Data for the pages, through TanStack Query: one request per question however many components ask,
   the previous answer stays on screen while a new one loads, and pages refresh on their own. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, DEMO, type ActionResult, type AuditFilter, type TimeRange } from "@/lib/api";

const live = (ms: number) => (DEMO ? false : ms);

export const keys = {
  me: ["me"] as const,
  health: ["health"] as const,
  overview: ["overview"] as const,
  customers: ["customers"] as const,
  devices: ["devices"] as const,
  customer: (code: string) => ["customer", code] as const,
  topology: (code: string) => ["topology", code] as const,
  device: (code: string, name: string) => ["device", code, name] as const,
  metrics: (id: string, range: TimeRange, charts: string[]) => ["metrics", id, range, charts.join(",")] as const,
  work: ["work"] as const,
  service: (month: string | undefined) => ["service", month ?? "current"] as const,
  audit: (filter: AuditFilter) => ["audit", filter] as const,
  sessions: ["sessions"] as const,
  glue: ["glue"] as const,
};

export const useMe = () => useQuery({ queryKey: keys.me, queryFn: () => api.me(), staleTime: 5 * 60_000 });
export const useHealth = () => useQuery({ queryKey: keys.health, queryFn: () => api.health(), refetchInterval: live(30_000) });
export const useOverview = () =>
  useQuery({ queryKey: keys.overview, queryFn: () => api.overview(), refetchInterval: live(30_000), placeholderData: keepPreviousData });
export const useCustomers = () =>
  useQuery({ queryKey: keys.customers, queryFn: () => api.customers(), refetchInterval: live(60_000), placeholderData: keepPreviousData });
export const useAllDevices = (enabled = true) =>
  useQuery({ queryKey: keys.devices, queryFn: () => api.allDevices(), enabled, staleTime: 60_000 });
export const useCustomer = (code: string) =>
  useQuery({ queryKey: keys.customer(code), queryFn: () => api.customer(code), enabled: Boolean(code), refetchInterval: live(30_000) });
export const useTopology = (code: string) =>
  useQuery({ queryKey: keys.topology(code), queryFn: () => api.topology(code), refetchInterval: live(60_000) });
export const useDevice = (code: string, name: string) =>
  useQuery({ queryKey: keys.device(code, name), queryFn: () => api.device(code, name), refetchInterval: live(30_000) });
export const useMetrics = (id: string | undefined, range: TimeRange, charts: string[]) =>
  useQuery({
    queryKey: keys.metrics(id ?? "", range, charts),
    queryFn: () => api.metrics(id!, range, charts),
    enabled: Boolean(id) && charts.length > 0,
    placeholderData: keepPreviousData,
    refetchInterval: live(60_000),
  });
export const useWork = () => useQuery({ queryKey: keys.work, queryFn: () => api.work(), refetchInterval: live(60_000) });
export const useService = (month?: string) =>
  useQuery({ queryKey: keys.service(month), queryFn: () => api.service(month), placeholderData: keepPreviousData });
export const useAudit = (filter: AuditFilter) =>
  useQuery({ queryKey: keys.audit(filter), queryFn: () => api.audit(filter), placeholderData: keepPreviousData, refetchInterval: live(30_000) });
export const useSessions = () => useQuery({ queryKey: keys.sessions, queryFn: () => api.sessions(), refetchInterval: live(60_000) });

/** After a change, read everything again (the demo also again a few seconds later, when its follow-ups finish). */
export function useRefreshAll() {
  const client = useQueryClient();
  return () => {
    const refresh = () => client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "me" && q.queryKey[0] !== "metrics" });
    void refresh();
    for (const ms of DEMO ? [1700, 5200, 7300, 9000] : [4000, 15_000]) setTimeout(() => void refresh(), ms);
  };
}

/**
 * An action against the platform. The toast says what happened in the console's words; an error keeps
 * the dialog open so the person can fix what the message names.
 */
export function useAction<T>(run: (input: T) => Promise<ActionResult>) {
  const refreshAll = useRefreshAll();
  return useMutation({
    mutationFn: run,
    onSuccess: (result) => {
      toast.success(result.message);
      refreshAll();
    },
  });
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
