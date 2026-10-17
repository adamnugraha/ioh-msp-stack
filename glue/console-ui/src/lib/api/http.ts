/* The console's own JSON API (FastAPI, same origin). The session cookie carries the sign-in;
   every change also sends the CSRF token the API handed out with /me. */
import {
  ApiError,
  type ActionResult,
  type AuditFilter,
  type AuditPage,
  type ConsoleApi,
  type CustomerDetail,
  type CustomerSummary,
  type DeviceDetail,
  type DeviceIndexRow,
  type DeviceMetrics,
  type DeviceRequest,
  type GlueStatus,
  type Investigation,
  type MaintenanceRequest,
  type Me,
  type NoteRequest,
  type Overview,
  type PlaybookRequest,
  type ServiceReport,
  type SessionRow,
  type TicketRequest,
  type TimeRange,
  type ToolStatus,
  type Topology,
  type WorkList,
} from "./types";

const BASE = "/api/v1";
let csrfToken = "";

function query(params: Record<string, string | undefined>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  return pairs.length ? "?" + new URLSearchParams(pairs as [string, string][]).toString() : "";
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = csrfToken;
  }
  let response: Response;
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "The console did not answer. Check the network connection, then try again.");
  }
  if (response.status === 401) {
    if (method === "GET") {
      const next = window.location.pathname + window.location.search;
      window.location.assign(`/auth/login?next=${encodeURIComponent(next)}`);
    }
    // A change sent after the session ended: nothing was done, and the person needs to know that.
    throw new ApiError(401, "Your session ended before this was sent, so nothing was done. Sign in again and repeat it.");
  }
  const json = (response.headers.get("content-type") ?? "").includes("application/json")
    ? await response.json().catch(() => null)
    : null;
  if (!response.ok) {
    const detail = json && typeof json.detail === "string" ? json.detail : `The console answered HTTP ${response.status}.`;
    throw new ApiError(response.status, detail);
  }
  return json as T;
}

const get = <T,>(path: string) => call<T>("GET", path);
const post = <T,>(path: string, body: unknown = {}) => call<T>("POST", path, body);
const enc = encodeURIComponent;

export function createHttpApi(): ConsoleApi {
  return {
    demo: false,
    async me() {
      const me = await get<Me>("/me");
      csrfToken = me.csrf;
      return me;
    },
    health: () => get<ToolStatus[]>("/health"),
    overview: () => get<Overview>("/overview"),
    customers: () => get<CustomerSummary[]>("/customers"),
    allDevices: () => get<DeviceIndexRow[]>("/devices"),
    customer: (code) => get<CustomerDetail>(`/customers/${enc(code)}`),
    topology: (code) => get<Topology>(`/customers/${enc(code)}/topology`),
    device: (code, name) => get<DeviceDetail>(`/customers/${enc(code)}/devices/${enc(name)}`),
    metrics: (deviceId: string, range: TimeRange, charts: string[]) =>
      get<DeviceMetrics>(`/devices/${enc(deviceId)}/metrics${query({ range, charts: charts.join(",") })}`),
    work: () => get<WorkList>("/work"),
    service: (month) => get<ServiceReport>(`/service${query({ month })}`),
    audit: (filter: AuditFilter) => get<AuditPage>(`/audit${query({ ...filter })}`),
    sessions: () => get<SessionRow[]>("/sessions"),
    glue: () => get<GlueStatus>("/glue"),

    acknowledge: (eventid, note) => post<ActionResult>(`/problems/${enc(eventid)}/acknowledge`, { note }),
    startInvestigation: (eventid) => post<Investigation>(`/problems/${enc(eventid)}/investigations`),
    investigation: (id) => get<Investigation>(`/investigations/${enc(id)}`),
    postNote: (req: NoteRequest) => post<ActionResult>("/notes", req),
    resync: (code) => post<ActionResult>(`/customers/${enc(code)}/resync`),
    configCheck: (code) => post<ActionResult>(`/customers/${enc(code)}/config-check`),
    runPlaybook: (req: PlaybookRequest) => post<ActionResult>(`/customers/${enc(req.customer)}/playbooks`, req),
    createTicket: (req: TicketRequest) => post<ActionResult>(`/customers/${enc(req.customer)}/tickets`, req),
    createMaintenance: (req: MaintenanceRequest) =>
      post<ActionResult>(`/customers/${enc(req.customer)}/maintenance`, req),
    addDevice: (req: DeviceRequest) => post<ActionResult>(`/customers/${enc(req.customer)}/devices`, req),

    exportUrl: (kind, params = {}) => `${BASE}/${kind}.csv${query(params)}`,
  };
}
