/*
 * The contract between the MSP Console front end and its back end (FastAPI, /api/v1).
 *
 * The demo build implements the same interface with sample data, so every page is written once
 * against these types. Times are ISO 8601 strings; the console shows them in the platform's time
 * zone (Me.timezone). Numbers are numbers, never formatted text.
 */

export type Role = "manager" | "engineer" | "automation";
export type ToolName = "Nautobot" | "Zabbix" | "GLPI" | "Semaphore" | "Forgejo" | "Grafana" | "Keycloak";
export type Tier = "gold" | "silver" | "bronze";
export type Severity = 0 | 1 | 2 | 3 | 4 | 5;
export type HostState = "up" | "down" | "unknown" | "disabled";
export type DeviceStatus = "Active" | "Planned" | "Staged" | "Offline" | "Decommissioning" | "Inventory";
export type DeviceRole = "router" | "firewall" | "switch" | "server" | "wireless";
export type Outcome =
  | "started"
  | "queued"
  | "running"
  | "done"
  | "success"
  | "failed"
  | "error"
  | "errors"
  | "stopped"
  | "refused"
  | "unknown";

// ---------------------------------------------------------------------------- who is signed in
export interface Me {
  email: string;
  name: string;
  roles: Role[];
  /** engineer: acknowledge, re-sync, configuration check, tickets, maintenance, devices, investigate */
  canAct: boolean;
  /** automation: start playbooks */
  canRun: boolean;
  isManager: boolean;
  /** sent back in the X-CSRF-Token header of every change */
  csrf: string;
  sessionEnds: string;
  /** Keycloak account page: password, two-factor, sessions */
  accountUrl: string;
  timezone: string;
  platformDomain: string;
  /** Investigate is set up: an administrator gave the console a model endpoint (CONSOLE_AI_URL) */
  investigate: boolean;
}

// ---------------------------------------------------------------------------- tools and sign-in
export interface SignIn {
  /** oidc and saml go through Keycloak; local means the tool's own login */
  method: "oidc" | "saml" | "local";
  label: string;
  /** which Keycloak role becomes which permission in the tool */
  mapping: string;
  enabled: boolean;
}

export interface ToolStatus {
  tool: ToolName;
  url: string;
  purpose: string;
  ok: boolean;
  version: string | null;
  ms: number | null;
  detail: string;
  signIn: SignIn;
}

export interface SourceError {
  source: string;
  error: string;
  since: string | null;
}

/** When the data on a page was read, and which tools did not answer. */
export interface Freshness {
  loaded: string;
  errors: SourceError[];
}

// ---------------------------------------------------------------------------- customers
export interface Sla {
  /** target, percent */
  slo: number;
  /** this month so far, percent; null before the agreement has data */
  sli: number | null;
  previous: number | null;
  /** seconds of downtime this month */
  downtime: number;
  /** downtime the target allows for the whole month, seconds */
  allowed: number;
}

export interface LastRun {
  status: Task["status"];
  when: string;
  template: string;
}

export interface CustomerSummary {
  code: string;
  name: string;
  tier: Tier;
  regulated: boolean;
  provisioning: "provisioned" | "pending" | "error";
  provisioningMessage: string;
  customerId: string;
  contactEmail: string;
  devices: number;
  active: number;
  up: number;
  down: number;
  /** of the devices down, how many are in a maintenance window */
  downInMaintenance: number;
  unknown: number;
  unmonitored: number;
  problems: number;
  worst: Severity | null;
  high: number;
  unacknowledged: number;
  tickets: number;
  late: number;
  changesToday: number;
  maintenanceNow: number;
  compliant: number;
  checked: number;
  inScope: number;
  lastBackup: string | null;
  backupFailed: number;
  lastRun: LastRun | null;
  sla: Sla | null;
}

export type CustomerLink = "nautobot" | "zabbix" | "glpi" | "semaphore" | "forgejo" | "grafana";

export interface Site {
  id: string;
  name: string;
  region: string;
}

export interface CustomerDetail {
  summary: CustomerSummary;
  links: Partial<Record<CustomerLink, string>>;
  sites: Site[];
  devices: Device[];
  problems: Problem[];
  tickets: Ticket[];
  changes: Change[];
  maintenance: Maintenance[];
  automation: Automation;
  audit: AuditEntry[];
  /** downtime minutes per day of the current month, for the availability chart */
  downtimeByDay: { day: string; minutes: number }[];
  freshness: Freshness;
}

// ---------------------------------------------------------------------------- devices
export interface Device {
  id: string;
  name: string;
  serial: string;
  status: DeviceStatus;
  role: DeviceRole;
  platform: string;
  model: string;
  site: string;
  customer: string;
  ip: string;
  /** Golden Config backs up and checks it: active, has an address, platform supported */
  gcScope: boolean;
  /** false when someone switched monitoring off in Nautobot */
  monitoring: boolean;
  driftTicket: number | null;
  host: { hostid: string; state: HostState; error: string; maintenance: boolean } | null;
  check: { rules: number; failed: string[]; checked: string | null } | null;
  backup: { lastSuccess: string | null; lastAttempt: string | null } | null;
  links: { nautobot: string; zabbix?: string; backups?: string; driftTicket?: string };
}

export interface Interface {
  name: string;
  description: string;
  status: "up" | "down" | "admin-down";
  /** bits per second */
  speed: number;
  inBps: number | null;
  outBps: number | null;
  errorsPerMin: number | null;
  peer: { device: string; interface: string } | null;
  circuit: string | null;
}

export interface Backup {
  at: string;
  commit: string;
  changedLines: number;
  message: string;
  link: string;
}

export interface ComplianceRule {
  rule: string;
  compliant: boolean;
  /** lines the intended configuration has and the device does not */
  missing: string[];
  /** lines on the device the intended configuration does not have */
  extra: string[];
}

export interface DeviceDetail {
  device: Device;
  customerName: string;
  interfaces: Interface[];
  problems: Problem[];
  tickets: Ticket[];
  tasks: Task[];
  backups: Backup[];
  compliance: ComplianceRule[];
  maintenance: Maintenance[];
  /** metric charts the device has in Zabbix, for the chart picker */
  charts: ChartInfo[];
  freshness: Freshness;
}

// ---------------------------------------------------------------------------- metrics (Zabbix)
export type TimeRange = "1h" | "6h" | "24h" | "7d" | "30d";

export interface ChartInfo {
  id: string;
  title: string;
  units: "ms" | "%" | "bps" | "count";
  /** interface charts name the interface */
  interface?: string;
}

export interface Series {
  key: string;
  name: string;
  /** [epoch milliseconds, value]; null where Zabbix has no value (device unreachable) */
  points: [number, number | null][];
}

export interface MetricChart extends ChartInfo {
  series: Series[];
}

export interface DeviceMetrics {
  range: TimeRange;
  /** history: raw values (up to 7 days); trends: hourly averages */
  source: "history" | "trends";
  step: number;
  charts: MetricChart[];
}

// ---------------------------------------------------------------------------- topology (Nautobot cables)
export interface TopoNode {
  id: string;
  name: string;
  role: DeviceRole | "network";
  site: string | null;
  /** unmonitored: monitoring switched off in Nautobot */
  state: HostState | "planned" | "external" | "unmonitored";
  maintenance: boolean;
  ip: string;
}

export interface TopoLink {
  id: string;
  a: string;
  aInterface: string;
  b: string;
  bInterface: string;
  speed: number;
  state: "up" | "down" | "unknown";
  kind: "cable" | "circuit";
  circuit: string | null;
}

export interface Topology {
  sites: Site[];
  nodes: TopoNode[];
  links: TopoLink[];
}

// ---------------------------------------------------------------------------- problems (Zabbix)
export interface ProblemUpdate {
  at: string;
  who: string;
  message: string;
}

export interface Problem {
  eventid: string;
  name: string;
  severity: Severity;
  started: string;
  acknowledged: boolean;
  /** in a Zabbix maintenance window: shown, but Zabbix sends no alert for it */
  suppressed: boolean;
  customer: string;
  customerName: string;
  device: string;
  site: string;
  ticketId: number | null;
  /** the ticket in GLPI, when there is one */
  ticketLink: string | null;
  opdata: string;
  /** the problem in Zabbix */
  link: string;
  updates: ProblemUpdate[];
}

// ---------------------------------------------------------------------------- tickets and changes (GLPI)
export type TicketStatus = "New" | "Assigned" | "Planned" | "Waiting" | "Solved" | "Closed";

export interface Ticket {
  id: number;
  name: string;
  type: "Incident" | "Request";
  status: TicketStatus;
  /** 1 very low ... 6 major */
  priority: 1 | 2 | 3 | 4 | 5 | 6;
  customer: string;
  opened: string;
  due: string | null;
  late: boolean;
  group: string;
  technician: string;
  /** GLPI: entered in GLPI itself (customer portal or a technician); Other: any other request source */
  source: "Monitoring" | "Configuration drift" | "Phone" | "Email" | "MSP Console" | "GLPI" | "Other";
  device: string | null;
  link: string;
}

export type ChangeStatus =
  | "New"
  | "Evaluation"
  | "Approval"
  | "Accepted"
  | "Pending"
  | "Test"
  | "Qualification"
  | "Applied"
  | "Review"
  | "Closed"
  | "Refused"
  | "Cancelled";

export interface Change {
  id: number;
  name: string;
  status: ChangeStatus;
  customer: string;
  category: string;
  /** Semaphore template the change runs when it is accepted, if any */
  template: string | null;
  plannedStart: string | null;
  plannedEnd: string | null;
  risk: "Low" | "Medium" | "High";
  devices: string[];
  requester: string;
  maintenanceId: string | null;
  link: string;
}

// ---------------------------------------------------------------------------- maintenance (Zabbix)
export interface Maintenance {
  id: string;
  name: string;
  customer: string;
  /** null: every device of the customer */
  devices: string[] | null;
  start: string;
  end: string;
  /** true: Zabbix keeps collecting data, problems are hidden from alerts */
  collectData: boolean;
  changeId: number | null;
  createdBy: string;
  state: "active" | "upcoming" | "ended";
  link: string;
}

export interface WorkList {
  changes: Change[];
  maintenance: Maintenance[];
  freshness: Freshness;
}

// ---------------------------------------------------------------------------- automation (Semaphore)
export interface Template {
  id: number;
  name: string;
  description: string;
  /** Semaphore lets a run choose its devices (allow_override_limit); without it the console refuses */
  limit: boolean;
  /** changes devices: needs a GLPI change in status Accepted */
  changeOnly: boolean;
}

export interface Task {
  id: number;
  template: string;
  status: "waiting" | "running" | "success" | "error" | "stopped";
  created: string;
  finished: string | null;
  message: string;
  devices: string[];
  by: string;
  item: string;
  customer: string;
  link: string;
}

export interface Automation {
  projectId: number | null;
  templates: Template[];
  tasks: Task[];
  link: string | null;
}

// ---------------------------------------------------------------------------- service levels
export interface ServiceRow {
  code: string;
  name: string;
  tier: Tier;
  regulated: boolean;
  sla: Sla | null;
  met: boolean | null;
  /** availability per month, oldest first, for the trend */
  history: { month: string; sli: number | null }[];
  opened: number;
  solved: number;
  breaches: number;
  ownLate: number;
  resolveLate: number;
  mttrHours: number | null;
  open: number;
  openLate: number;
  compliance: number | null;
  backup: number | null;
  compliantN: number;
  checkedN: number;
  freshN: number;
  netN: number;
}

export interface ServiceReport {
  month: string;
  label: string;
  monthsAvailable: { month: string; label: string }[];
  rows: ServiceRow[];
  totals: {
    customers: number;
    withSla: number;
    met: number;
    opened: number;
    solved: number;
    breaches: number;
    open: number;
    openLate: number;
    mttrHours: number | null;
    compliance: number | null;
    backup: number | null;
  };
  freshness: Freshness;
}

// ---------------------------------------------------------------------------- audit trail
export interface AuditEntry {
  id: number;
  at: string;
  email: string;
  name: string;
  action: string;
  customer: string;
  target: string;
  ticket: string;
  reason: string;
  outcome: Outcome;
  result: string;
  address: string;
  updated: string | null;
}

export interface AuditFilter {
  customer?: string;
  email?: string;
  action?: string;
  outcome?: string;
}

export interface AuditPage {
  entries: AuditEntry[];
  choices: { customer: string[]; email: string[]; action: string[]; outcome: string[] };
}

export interface SessionRow {
  email: string;
  name: string;
  roles: Role[];
  since: string;
  lastSeen: string;
  address: string;
}

// ---------------------------------------------------------------------------- msp-glue
export interface GlueJob {
  id: number;
  name: string;
  status: "queued" | "running" | "done" | "failed";
  started: string;
  finished: string | null;
  result: string;
  by: string;
}

export interface GlueStatus {
  queue: number;
  lastReconcile: string | null;
  lastError: { at: string; message: string } | null;
  jobs: GlueJob[];
}

// ---------------------------------------------------------------------------- overview
export interface Overview {
  problems: Problem[];
  customers: CustomerSummary[];
  tickets: Ticket[];
  changesToday: Change[];
  maintenance: Maintenance[];
  glue: GlueStatus | null;
  devicesTotal: number;
  freshness: Freshness;
}

// ---------------------------------------------------------------------------- investigate
export interface InvestigationStep {
  id: string;
  tool: ToolName;
  title: string;
  status: "waiting" | "running" | "done" | "failed";
  finding: string;
  link: string | null;
}

export interface NextStep {
  text: string;
  /** a read-only playbook the engineer can start from the panel */
  playbook?: { template: string; devices: string[] };
}

export interface Investigation {
  id: string;
  eventid: string;
  started: string;
  status: "running" | "done" | "failed";
  /** the OpenAI-compatible endpoint and model that wrote the draft */
  model: { name: string; endpoint: string };
  steps: InvestigationStep[];
  cause: string | null;
  confidence: "low" | "medium" | "high" | null;
  nextSteps: NextStep[];
  /** text for the ticket, ready to post as a private follow-up */
  draftNote: string | null;
  /** sample answer: no model was called (demo build) */
  sample: boolean;
  /** why the run failed (status failed): the model endpoint did not give a usable answer */
  error?: string | null;
}

// ---------------------------------------------------------------------------- requests
export interface PlaybookRequest {
  customer: string;
  templateId: number;
  devices: string[];
  itemType: "Ticket" | "Change";
  itemId: number;
  reason: string;
  variables: string;
}

export interface TicketRequest {
  customer: string;
  kind: "Incident" | "Request" | "Change";
  title: string;
  description: string;
  /** 1 very low ... 5 very high */
  priority: 1 | 2 | 3 | 4 | 5;
  devices: string[];
  /** changes only */
  plannedStart?: string;
  plannedEnd?: string;
  risk?: "Low" | "Medium" | "High";
  template?: string | null;
  rollback?: string;
}

export interface MaintenanceRequest {
  customer: string;
  /** null: every device of the customer */
  devices: string[] | null;
  name: string;
  start: string;
  minutes: number;
  collectData: boolean;
  changeId: number | null;
}

export interface DeviceRequest {
  customer: string;
  site: string;
  name: string;
  role: DeviceRole;
  platform: string;
  model: string;
  status: "Active" | "Planned";
  ip: string;
  serial: string;
  monitoring: boolean;
}

export interface NoteRequest {
  itemType: "Ticket" | "Change";
  itemId: number;
  text: string;
}

export interface ActionResult {
  message: string;
  auditId: number;
  /** id of what was created: ticket, change, task, maintenance or device */
  id?: number | string;
}

/** Every device of every customer, light enough for search. */
export interface DeviceIndexRow {
  name: string;
  customer: string;
  customerName: string;
  site: string;
  role: DeviceRole;
  ip: string;
  status: DeviceStatus;
  state: HostState | null;
}

// ---------------------------------------------------------------------------- the client
export interface ConsoleApi {
  /** true in the demo build: sample data, nothing leaves the page */
  readonly demo: boolean;
  me(): Promise<Me>;
  health(): Promise<ToolStatus[]>;
  overview(): Promise<Overview>;
  customers(): Promise<CustomerSummary[]>;
  allDevices(): Promise<DeviceIndexRow[]>;
  customer(code: string): Promise<CustomerDetail>;
  topology(code: string): Promise<Topology>;
  device(code: string, name: string): Promise<DeviceDetail>;
  metrics(deviceId: string, range: TimeRange, charts: string[]): Promise<DeviceMetrics>;
  work(): Promise<WorkList>;
  service(month?: string): Promise<ServiceReport>;
  audit(filter: AuditFilter): Promise<AuditPage>;
  sessions(): Promise<SessionRow[]>;
  glue(): Promise<GlueStatus>;

  acknowledge(eventid: string, note: string): Promise<ActionResult>;
  startInvestigation(eventid: string): Promise<Investigation>;
  investigation(id: string): Promise<Investigation>;
  postNote(req: NoteRequest): Promise<ActionResult>;
  resync(code: string): Promise<ActionResult>;
  configCheck(code: string): Promise<ActionResult>;
  runPlaybook(req: PlaybookRequest): Promise<ActionResult>;
  createTicket(req: TicketRequest): Promise<ActionResult>;
  createMaintenance(req: MaintenanceRequest): Promise<ActionResult>;
  addDevice(req: DeviceRequest): Promise<ActionResult>;

  /** where a CSV export downloads from; null when there is nothing to download (demo) */
  exportUrl(kind: "service" | "audit", params?: Record<string, string>): string | null;
  /** demo only: look at the console with other roles */
  setPreviewRoles?(roles: Role[]): Promise<Me>;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}
