/*
 * The preview's API: the same interface as the real one, answered from sample data in the page.
 * Actions follow the console's rules (roles, approved changes for changing templates, device limits)
 * and change the sample state, so a click shows what the console would do. Nothing leaves the page.
 */
import {
  ApiError,
  type AuditEntry,
  type Change,
  type ConsoleApi,
  type CustomerDetail,
  type CustomerSummary,
  type Device,
  type Freshness,
  type Investigation,
  type Maintenance,
  type Me,
  type Problem,
  type ServiceRow,
  type Sla,
  type Task,
  type Ticket,
  type Topology,
} from "../types";
import { DAY, dayKey, hash, HOUR, iso, MIN, monthKey, monthLabel, monthStart, wibMidnight } from "./clock";
import { scriptFor, type Script } from "./investigations";
import { chartsFor, interfacesFor, metricsFor } from "./metrics";
import {
  buildState,
  complianceRules,
  type DemoCustomer,
  type DemoState,
  GC_PLATFORMS,
  meFor,
  refreshLinks,
  slug,
  STAFF,
  toolUrl,
} from "./sample";

const SLO = { gold: 99.9, silver: 99.5, bronze: 99.0 } as const;
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const read = () => wait(120 + Math.random() * 220);
const act = () => wait(380 + Math.random() * 380);
const fail = (message: string, status = 400): never => {
  throw new ApiError(status, message);
};

const fmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" });
const fmtTime = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" });

export function createDemoApi(): ConsoleApi {
  const state: DemoState = buildState(Date.now());
  const investigations = new Map<string, { inv: Investigation; script: Script; startedAt: number }>();
  const freshness = (): Freshness => ({ loaded: iso(Date.now() - 12_000), errors: [] });
  const customer = (code: string): DemoCustomer =>
    state.customers.find((c) => c.code === code) ?? fail(`No managed customer with code ${code}.`, 404);
  const devicesOf = (code: string) => state.devices.filter((x) => x.customer === code);
  const me = () => state.me;
  const needEngineer = (what: string) => {
    if (!me().canAct) fail(`${what} needs the engineer role.`, 403);
  };

  function audit(entry: Omit<AuditEntry, "id" | "at" | "email" | "name" | "address" | "updated">): AuditEntry {
    const row: AuditEntry = {
      id: state.ids.audit++, at: iso(Date.now()), email: me().email, name: me().name, address: "203.0.113.24", updated: null, ...entry,
    };
    state.audit.unshift(row);
    return row;
  }
  const finish = (row: AuditEntry, outcome: AuditEntry["outcome"], result: string) => {
    row.outcome = outcome;
    row.result = result;
    row.updated = iso(Date.now());
  };
  const later = (ms: number, f: () => void) => setTimeout(f, ms);

  // ----------------------------------------------------------------------------- derived values
  function sla(c: DemoCustomer): Sla | null {
    if (c.provisioning === "error") return null;
    const now = Date.now();
    const start = monthStart(now);
    const prevStart = monthStart(now, -1);
    const minutesIn = (from: number, to: number) =>
      state.outages
        .filter((o) => o.customer === c.code && o.counted && o.start >= from && o.start < to)
        .reduce((sum, o) => sum + (o.minutes ?? (now - o.start) / MIN), 0);
    const down = minutesIn(start, now);
    const prevDown = minutesIn(prevStart, start);
    const elapsed = (now - start) / MIN;
    const monthMinutes = (monthStart(now, 1) - start) / MIN;
    const prevMinutes = (start - prevStart) / MIN;
    const slo = SLO[c.tier];
    return {
      slo,
      sli: +(100 * (1 - down / elapsed)).toFixed(3),
      previous: +(100 * (1 - prevDown / prevMinutes)).toFixed(3),
      downtime: Math.round(down * 60),
      allowed: Math.round(monthMinutes * 60 * (1 - slo / 100)),
    };
  }

  function sortProblems(list: Problem[]): Problem[] {
    return [...list].sort((a, b) =>
      Number(a.acknowledged) - Number(b.acknowledged) || Number(a.suppressed) - Number(b.suppressed) ||
      b.severity - a.severity || a.started.localeCompare(b.started));
  }

  function sortTickets(list: Ticket[]): Ticket[] {
    return [...list].sort((a, b) =>
      Number(b.late) - Number(a.late) || (a.due ?? "9").localeCompare(b.due ?? "9") || b.priority - a.priority);
  }

  function changesToday(list: Change[]): Change[] {
    const now = Date.now();
    const from = wibMidnight(now);
    const to = from + DAY + 6 * HOUR;
    return list.filter((ch) => {
      if (!ch.plannedStart || !ch.plannedEnd) return false;
      if (["Applied", "Closed", "Refused", "Cancelled", "Review"].includes(ch.status)) return false;
      const s = Date.parse(ch.plannedStart);
      const e = Date.parse(ch.plannedEnd);
      return e >= now && s <= to;
    });
  }

  function maintenanceState(m: Maintenance): Maintenance {
    const now = Date.now();
    const s = Date.parse(m.start);
    const e = Date.parse(m.end);
    return { ...m, state: now < s ? "upcoming" : now > e ? "ended" : "active" };
  }

  function summary(c: DemoCustomer): CustomerSummary {
    const mine = devicesOf(c.code);
    const active = mine.filter((x) => x.status === "Active");
    const hosts = active.filter((x) => x.host);
    const probs = state.problems.filter((p) => p.customer === c.code);
    const tickets = state.tickets.filter((t) => t.customer === c.code);
    const checked = active.filter((x) => x.check);
    const backups = active.filter((x) => x.backup?.lastSuccess).map((x) => x.backup!.lastSuccess!);
    const failed = active.filter((x) => x.backup && x.backup.lastAttempt && x.backup.lastSuccess && x.backup.lastAttempt > x.backup.lastSuccess);
    const tasks = state.tasks.filter((t) => t.customer === c.code).sort((a, b) => b.created.localeCompare(a.created));
    const worst = probs.length ? (Math.max(...probs.map((p) => p.severity)) as CustomerSummary["worst"]) : null;
    const maint = state.maintenance.map(maintenanceState).filter((m) => m.customer === c.code && m.state === "active");
    return {
      code: c.code, name: c.name, tier: c.tier, regulated: c.regulated, provisioning: c.provisioning,
      provisioningMessage: c.provisioningMessage, customerId: c.customerId, contactEmail: c.contactEmail,
      devices: mine.length, active: active.length,
      up: hosts.filter((x) => x.host!.state === "up").length,
      down: hosts.filter((x) => x.host!.state === "down").length,
      downInMaintenance: hosts.filter((x) => x.host!.state === "down" && x.host!.maintenance).length,
      unknown: hosts.filter((x) => x.host!.state === "unknown").length,
      unmonitored: active.filter((x) => !x.host && x.monitoring).length,
      problems: probs.length, worst, high: probs.filter((p) => p.severity >= 4).length,
      unacknowledged: probs.filter((p) => !p.acknowledged).length,
      tickets: tickets.length, late: tickets.filter((t) => t.late).length,
      changesToday: changesToday(state.changes.filter((ch) => ch.customer === c.code)).length,
      maintenanceNow: maint.length,
      compliant: checked.filter((x) => !x.check!.failed.length).length, checked: checked.length,
      inScope: active.filter((x) => x.gcScope).length,
      lastBackup: backups.sort().at(-1) ?? null, backupFailed: failed.length,
      lastRun: tasks[0] ? { status: tasks[0].status, when: tasks[0].created, template: tasks[0].template } : null,
      sla: sla(c),
    };
  }

  function summaries(): CustomerSummary[] {
    return state.customers.map(summary).sort((a, b) =>
      (b.worst ?? -1) - (a.worst ?? -1) || b.late - a.late || a.name.localeCompare(b.name));
  }

  function links(c: DemoCustomer): CustomerDetail["links"] {
    return {
      nautobot: toolUrl("nautobot", `/tenancy/tenants/?q=${encodeURIComponent(c.name)}`),
      zabbix: c.hostgroupId ? toolUrl("zabbix", `/zabbix.php?action=problem.view&filter_set=1&groupids%5B%5D=${c.hostgroupId}`) : undefined,
      glpi: c.entityId ? toolUrl("glpi", `/front/ticket.php?criteria%5B0%5D%5Bfield%5D=80&criteria%5B0%5D%5Bvalue%5D=${c.entityId}`) : undefined,
      semaphore: c.projectId ? toolUrl("semaphore", `/project/${c.projectId}/history`) : undefined,
      forgejo: toolUrl("forgejo", `/msp/backups-${c.code}`),
      grafana: c.grafanaOrg ? toolUrl("grafana", `/dashboards?orgId=${c.grafanaOrg}`) : undefined,
    };
  }

  function downtimeByDay(c: DemoCustomer) {
    const now = Date.now();
    const out: { day: string; minutes: number }[] = [];
    for (let t = monthStart(now); t <= now; t += DAY) {
      const end = t + DAY;
      const minutes = state.outages
        .filter((o) => o.customer === c.code && o.counted && o.start >= t && o.start < end)
        .reduce((sum, o) => sum + (o.minutes ?? (now - o.start) / MIN), 0);
      out.push({ day: dayKey(t + HOUR), minutes: Math.round(minutes) });
    }
    return out;
  }

  const sortDevices = (list: Device[]) =>
    [...list].sort((a, b) => Number(a.status !== "Active") - Number(b.status !== "Active") || a.site.localeCompare(b.site) || a.name.localeCompare(b.name));

  function serviceRow(c: DemoCustomer): ServiceRow {
    const s = summary(c);
    const a = s.sla;
    const net = devicesOf(c.code).filter((x) => x.status === "Active" && x.gcScope);
    const dayAgo = new Date(Date.now() - 26 * HOUR).toISOString();
    const fresh = net.filter((x) => x.backup?.lastSuccess && x.backup.lastSuccess > dayAgo);
    const now = Date.now();
    const history = [...c.olderMonths.map((sli, i) => ({ month: monthKey(monthStart(now, i - 5)), sli })),
      { month: monthKey(monthStart(now, -1)), sli: a?.previous ?? null }, { month: monthKey(now), sli: a?.sli ?? null }];
    return {
      code: c.code, name: c.name, tier: c.tier, regulated: c.regulated, sla: a,
      met: a?.sli == null ? null : a.sli >= a.slo, history,
      opened: c.month.opened, solved: c.month.solved, breaches: c.month.ownLate + c.month.resolveLate,
      ownLate: c.month.ownLate, resolveLate: c.month.resolveLate, mttrHours: c.month.mttrHours,
      open: s.tickets, openLate: s.late,
      compliance: s.checked ? Math.round((100 * s.compliant) / s.checked) : null,
      backup: net.length ? Math.round((100 * fresh.length) / net.length) : null,
      compliantN: s.compliant, checkedN: s.checked, freshN: fresh.length, netN: net.length,
    };
  }

  function investigationNow(id: string): Investigation {
    const entry = investigations.get(id) ?? fail("That investigation is no longer kept. Start a new one.", 404);
    const elapsed = Date.now() - entry.startedAt;
    const per = 850;
    const steps = entry.script.steps.map((s, i) => ({
      ...s,
      status: elapsed >= (i + 1) * per ? ("done" as const) : elapsed >= i * per ? ("running" as const) : ("waiting" as const),
      finding: elapsed >= (i + 1) * per ? s.finding : "",
    }));
    const done = elapsed >= entry.script.steps.length * per + 900;
    entry.inv = {
      ...entry.inv, steps, status: done ? "done" : "running",
      cause: done ? entry.script.cause : null, confidence: done ? entry.script.confidence : null,
      nextSteps: done ? entry.script.nextSteps : [], draftNote: done ? entry.script.note : null,
    };
    return entry.inv;
  }

  // ------------------------------------------------------------------------------------- the API
  const api: ConsoleApi = {
    demo: true,

    async me(): Promise<Me> {
      await wait(60);
      return me();
    },

    async health() {
      await read();
      return state.tools.map((t) => ({ ...t, ms: t.ms === null ? null : Math.max(8, Math.round(t.ms * (0.8 + Math.random() * 0.4))) }));
    },

    async overview() {
      await read();
      const now = Date.now();
      return {
        problems: sortProblems(state.problems),
        customers: summaries(),
        tickets: sortTickets(state.tickets).slice(0, 12),
        changesToday: changesToday(state.changes),
        maintenance: state.maintenance.map(maintenanceState)
          .filter((m) => m.state === "active" || (m.state === "upcoming" && Date.parse(m.start) - now < DAY)),
        glue: me().canAct ? state.glue : null,
        devicesTotal: state.devices.length,
        freshness: freshness(),
      };
    },

    async customers() {
      await read();
      return summaries();
    },

    async allDevices() {
      await read();
      return state.devices.map((x) => ({
        name: x.name, customer: x.customer, customerName: customer(x.customer).name, site: x.site, role: x.role, ip: x.ip,
        status: x.status, state: x.host?.state ?? null,
      }));
    },

    async customer(code) {
      await read();
      const c = customer(code);
      const project = c.projectId;
      return {
        summary: summary(c),
        links: links(c),
        sites: c.sites,
        devices: sortDevices(devicesOf(code)),
        problems: sortProblems(state.problems.filter((p) => p.customer === code)),
        tickets: sortTickets(state.tickets.filter((t) => t.customer === code)),
        changes: state.changes.filter((ch) => ch.customer === code),
        maintenance: state.maintenance.map(maintenanceState).filter((m) => m.customer === code),
        automation: {
          projectId: project,
          templates: project ? state.templates[code] ?? [] : [],
          tasks: state.tasks.filter((t) => t.customer === code).sort((a, b) => b.created.localeCompare(a.created)),
          link: project ? toolUrl("semaphore", `/project/${project}/history`) : null,
        },
        audit: state.audit.filter((e) => e.customer === code).slice(0, 15),
        downtimeByDay: downtimeByDay(c),
        freshness: freshness(),
      };
    },

    async topology(code): Promise<Topology> {
      await read();
      const c = customer(code);
      const mine = devicesOf(code);
      const names = new Set(mine.map((x) => x.name));
      const linksOf = state.links.filter((l) => names.has(l.a) || names.has(l.b));
      const nodes: Topology["nodes"] = mine.map((x) => ({
        id: x.name, name: x.name, role: x.role, site: c.sites.find((s) => x.name.startsWith(s.id))?.id ?? null,
        state: x.status === "Planned" ? "planned" : x.host ? x.host.state : x.monitoring ? "unknown" : "unmonitored",
        maintenance: x.host?.maintenance ?? false, ip: x.ip,
      }));
      if (linksOf.some((l) => l.kind === "circuit")) {
        nodes.unshift({ id: `ioh-${code}`, name: "IOH network", role: "network", site: null, state: "external", maintenance: false, ip: "" });
      }
      return { sites: c.sites, nodes, links: linksOf };
    },

    async device(code, name) {
      await read();
      const c = customer(code);
      const device = devicesOf(code).find((x) => x.name === name) ?? fail(`${c.name} has no device called ${name}.`, 404);
      return {
        device,
        customerName: c.name,
        interfaces: interfacesFor(state, device),
        problems: sortProblems(state.problems.filter((p) => p.device === name)),
        tickets: sortTickets(state.tickets.filter((t) => t.device === name)),
        tasks: state.tasks.filter((t) => t.devices.includes(name)).sort((a, b) => b.created.localeCompare(a.created)),
        backups: backupsOf(device),
        compliance: complianceRules(device),
        maintenance: state.maintenance.map(maintenanceState).filter((m) => m.customer === code && (m.devices === null || m.devices.includes(name))),
        charts: chartsFor(state, device),
        freshness: freshness(),
      };
    },

    async metrics(deviceId, range, charts) {
      await wait(90 + Math.random() * 160);
      const device = state.devices.find((x) => x.id === deviceId) ?? fail("Unknown device.", 404);
      return metricsFor(state, device, range, charts);
    },

    async work() {
      await read();
      const order = (ch: Change) => (["Applied", "Closed", "Refused", "Cancelled", "Review"].includes(ch.status) ? 1 : 0);
      return {
        changes: [...state.changes].sort((a, b) => order(a) - order(b) || (a.plannedStart ?? "9").localeCompare(b.plannedStart ?? "9")),
        maintenance: state.maintenance.map(maintenanceState).sort((a, b) => a.start.localeCompare(b.start)),
        freshness: freshness(),
      };
    },

    async service(month) {
      await read();
      const now = Date.now();
      const current = monthKey(now);
      const rows = state.customers.map(serviceRow);
      if (month && month !== current) {
        // last month: the agreement's final numbers
        for (const r of rows) {
          if (r.sla) {
            const prev = r.sla.previous;
            r.sla = { ...r.sla, sli: prev, downtime: Math.round(((100 - (prev ?? 100)) / 100) * 30 * 24 * 3600) };
            r.met = prev == null ? null : prev >= r.sla.slo;
          }
          r.opened = Math.round(r.opened * 1.4);
          r.solved = r.opened - (r.code === "energi" ? 0 : 1);
          r.open = 0;
          r.openLate = 0;
        }
      }
      rows.sort((a, b) => Number(a.met !== false) - Number(b.met !== false) || a.name.localeCompare(b.name));
      const solvedWithTime = rows.filter((r) => r.mttrHours !== null);
      const totalSolved = solvedWithTime.reduce((s, r) => s + r.solved, 0);
      const ratio = (a: number, b: number) => (b ? Math.round((100 * a) / b) : null);
      const selected = month ?? current;
      return {
        month: selected,
        label: monthLabel(selected === current ? now : monthStart(now, -1)),
        monthsAvailable: [
          { month: current, label: `${monthLabel(now)} (so far)` },
          { month: monthKey(monthStart(now, -1)), label: monthLabel(monthStart(now, -1)) },
        ],
        rows,
        totals: {
          customers: rows.length,
          withSla: rows.filter((r) => r.met !== null).length,
          met: rows.filter((r) => r.met).length,
          opened: rows.reduce((s, r) => s + r.opened, 0),
          solved: rows.reduce((s, r) => s + r.solved, 0),
          breaches: rows.reduce((s, r) => s + r.breaches, 0),
          open: rows.reduce((s, r) => s + r.open, 0),
          openLate: rows.reduce((s, r) => s + r.openLate, 0),
          mttrHours: totalSolved ? +(solvedWithTime.reduce((s, r) => s + (r.mttrHours ?? 0) * r.solved, 0) / totalSolved).toFixed(1) : null,
          compliance: ratio(rows.reduce((s, r) => s + r.compliantN, 0), rows.reduce((s, r) => s + r.checkedN, 0)),
          backup: ratio(rows.reduce((s, r) => s + r.freshN, 0), rows.reduce((s, r) => s + r.netN, 0)),
        },
        freshness: freshness(),
      };
    },

    async audit(filter) {
      await read();
      const entries = state.audit.filter((e) =>
        (!filter.customer || e.customer === filter.customer) && (!filter.email || e.email === filter.email) &&
        (!filter.action || e.action === filter.action) && (!filter.outcome || e.outcome === filter.outcome));
      const distinct = (k: keyof AuditEntry) => [...new Set(state.audit.map((e) => String(e[k])).filter(Boolean))].sort();
      return {
        entries,
        choices: { customer: distinct("customer"), email: distinct("email"), action: distinct("action"), outcome: distinct("outcome") },
      };
    },

    async sessions() {
      await read();
      return state.sessions.map((s) => (s.email === me().email ? { ...s, roles: me().roles, lastSeen: iso(Date.now()) } : s));
    },

    async glue() {
      await read();
      return state.glue;
    },

    // ------------------------------------------------------------------------------- actions
    async acknowledge(eventid, noteText) {
      await act();
      needEngineer("Acknowledging problems");
      const p = state.problems.find((x) => x.eventid === eventid) ??
        fail("This problem is resolved already, or it is not on a device the platform manages.");
      const note = noteText.split(/\s+/).join(" ").trim().slice(0, 500);
      const already = p.acknowledged;
      if (already && !note) fail("This problem is acknowledged already. Write a note to add one.");
      const message = `${already ? "Note" : "Acknowledged"} in the MSP Console by ${me().name} (${me().email})${note ? `: ${note}` : "."}`;
      p.updates.unshift({ at: iso(Date.now()), who: me().name, message: note || "Acknowledged." });
      p.acknowledged = true;
      const row = audit({
        action: already ? "problem note" : "acknowledge", customer: p.customer, target: `${p.device}: ${p.name}`,
        ticket: p.ticketId ? `Ticket #${p.ticketId}` : "", reason: note, outcome: "done", result: message,
      });
      const done = already ? "Note added." : "Problem acknowledged.";
      return { message: done + (p.ticketId ? ` Zabbix also adds it to GLPI ticket #${p.ticketId}.` : ""), auditId: row.id };
    },

    async startInvestigation(eventid) {
      await wait(250);
      needEngineer("Investigating problems");
      const p = state.problems.find((x) => x.eventid === eventid) ?? fail("This problem is resolved already.");
      const id = `inv-${eventid}-${Date.now().toString(36)}`;
      const script = scriptFor(p);
      const inv: Investigation = {
        id, eventid, started: iso(Date.now()), status: "running",
        model: { name: "sample answer", endpoint: "no model called in the preview" },
        steps: script.steps.map((s) => ({ ...s, status: "waiting", finding: "" })),
        cause: null, confidence: null, nextSteps: [], draftNote: null, sample: true,
      };
      investigations.set(id, { inv, script, startedAt: Date.now() });
      return investigationNow(id);
    },

    async investigation(id) {
      await wait(80);
      return investigationNow(id);
    },

    async postNote(req) {
      await act();
      needEngineer("Adding notes to tickets");
      const text = req.text.trim();
      if (text.length < 10) fail("Write the note first (at least 10 characters).");
      const ticket = req.itemType === "Ticket" ? state.tickets.find((t) => t.id === req.itemId) : null;
      const change = req.itemType === "Change" ? state.changes.find((ch) => ch.id === req.itemId) : null;
      if (!ticket && !change) fail(`GLPI has no open ${req.itemType.toLowerCase()} #${req.itemId}.`);
      const what = ticket?.name ?? change?.name ?? "";
      const row = audit({
        action: "investigation note", customer: ticket?.customer ?? change?.customer ?? "", target: what,
        ticket: `${req.itemType} #${req.itemId}`, reason: "Draft cause checked and posted", outcome: "done",
        result: `Private follow-up added to ${req.itemType} #${req.itemId}.`,
      });
      return { message: `Private follow-up added to GLPI ${req.itemType.toLowerCase()} #${req.itemId}, marked as written with the assistant.`, auditId: row.id };
    },

    async resync(code) {
      await act();
      needEngineer("Re-syncing customers");
      const c = customer(code);
      const recent = state.audit.find((e) => e.action === "re-sync" && e.customer === code && e.email === me().email &&
        Date.now() - Date.parse(e.at) < 30_000);
      if (recent) fail("A re-sync of this customer was just queued. Wait for it to finish.");
      const tenantJob = state.ids.job++;
      const devicesJob = state.ids.job++;
      const row = audit({ action: "re-sync", customer: code, target: c.name, ticket: "", reason: "", outcome: "queued", result: "" });
      const job = (id: number, name: string) => ({
        id, name, status: "queued" as const, started: iso(Date.now()), finished: null as string | null, result: "", by: me().email,
      });
      state.glue.jobs.unshift(job(devicesJob, `devices ${code}`), job(tenantJob, `tenant ${code}`));
      state.glue.queue = 2;
      later(1500, () => {
        for (const j of state.glue.jobs) if (j.id === tenantJob || j.id === devicesJob) (j as { status: string }).status = "running";
      });
      later(5000, () => {
        const count = devicesOf(code).length;
        for (const j of state.glue.jobs) {
          if (j.id === tenantJob) Object.assign(j, { status: "done", finished: iso(Date.now()), result: code === "energi" ? "Semaphore project created; all tools set up" : "no changes" });
          if (j.id === devicesJob) Object.assign(j, { status: "done", finished: iso(Date.now()), result: `${count} devices, 0 changed` });
        }
        state.glue.queue = 0;
        if (c.provisioning === "error") {
          c.provisioning = "provisioned";
          c.provisioningMessage = "All tools set up";
          c.projectId = 7;
          state.templates[code] = state.templates.acme.filter((t) => t.id <= 3).map((t) => ({ ...t }));
          state.glue.lastError = null;
        }
        finish(row, "done", `tenant ${code}: done (${code === "energi" ? "Semaphore project created" : "no changes"}); devices ${code}: done (${count} devices, 0 changed)`);
      });
      return {
        message: `Re-sync of ${c.name} queued on msp-glue (jobs ${tenantJob}, ${devicesJob}). The result appears in the audit trail and under Integration jobs.`,
        auditId: row.id,
      };
    },

    async configCheck(code) {
      await act();
      needEngineer("Starting a configuration check");
      const c = customer(code);
      const scope = devicesOf(code).filter((x) => x.gcScope);
      if (!scope.length) {
        fail(`${c.name} has no active network device with a primary IP address and a platform Golden Config supports, so there is nothing to check.`);
      }
      if (state.audit.some((e) => e.action === "configuration check" && e.customer === code && e.outcome === "started")) {
        fail("A configuration check of this customer is already running.");
      }
      const row = audit({ action: "configuration check", customer: code, target: c.name, ticket: "", reason: "", outcome: "started", result: "" });
      later(7000, () => {
        const job = state.ids.job++;
        const now = iso(Date.now());
        const unreachable = scope.filter((x) => x.host?.state === "down" || x.name === "bdg01-rtr01");
        for (const x of scope) {
          if (!x.backup || !x.check) continue;
          x.backup.lastAttempt = now;
          if (!unreachable.includes(x)) {
            x.backup.lastSuccess = now;
            x.check.checked = now;
          }
        }
        if (unreachable.length) {
          finish(row, "errors", `Golden Config finished with errors: ${unreachable.map((x) => x.name).join(", ")} could not be backed up or checked. Drift tickets update queued on msp-glue (job ${job}).`);
        } else {
          finish(row, "done", `Golden Config backed up and checked every network device. Drift tickets update queued on msp-glue (job ${job}).`);
        }
      });
      return {
        message: `Golden Config is backing up and checking the network devices of ${c.name}. Drift tickets are updated when it finishes, usually within a few minutes.`,
        auditId: row.id,
      };
    },

    async runPlaybook(req) {
      await act();
      if (!me().canRun) fail("Starting playbooks needs the automation role.", 403);
      const c = customer(req.customer);
      if (!c.projectId) fail("This customer has no Semaphore project yet. Re-sync the customer first.");
      const template = (state.templates[c.code] ?? []).find((t) => t.id === req.templateId) ?? fail("Choose one of this customer's templates.");
      if (!template.limit) {
        fail(`'${template.name}' would run on every device of ${c.name}: the template does not let a run choose its devices. In Semaphore, edit the template and allow the limit to be set per run (allow_override_limit), then start it here.`);
      }
      const devices = [...new Set(req.devices.map((x) => x.trim()).filter(Boolean))].sort();
      if (!devices.length) fail("Choose at least one device.");
      const known = new Set(devicesOf(c.code).map((x) => x.name));
      const unknown = devices.filter((x) => !known.has(x));
      if (unknown.length) fail(`Not a device of ${c.name}: ${unknown.join(", ")}.`);
      const reason = req.reason.split(/\s+/).join(" ").trim();
      if (reason.length < 10) fail("Write why you run it (at least 10 characters). It goes into the ticket.");
      if (!Number.isInteger(req.itemId) || req.itemId <= 0) fail("Give the GLPI ticket or change number.");
      for (const [n, line] of req.variables.split("\n").entries()) {
        if (!line.trim() || line.trim().startsWith("#")) continue;
        const [key, ...rest] = line.split(":");
        if (!rest.length || !/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(key.trim())) fail(`Variables, line ${n + 1}: write one 'name: value' per line.`);
        if (key.trim().toLowerCase().startsWith("ansible_") || ["glpi_ticket_id", "glpi_change_id", "msp_console_user"].includes(key.trim())) {
          fail(`The variable ${key.trim()} cannot be set here.`);
        }
      }
      if (template.changeOnly && req.itemType !== "Change") {
        fail(`'${template.name}' changes devices, so it needs an approved GLPI change (status Accepted), not a ticket.`);
      }
      if (req.itemType === "Ticket") {
        const t = state.tickets.find((x) => x.id === req.itemId);
        if (!t) fail(`GLPI has no open ticket #${req.itemId}.`);
        if (t!.customer !== c.code) fail(`Ticket #${req.itemId} belongs to another customer.`);
      } else {
        const ch = state.changes.find((x) => x.id === req.itemId);
        if (!ch) fail(`GLPI has no change #${req.itemId}.`);
        if (ch!.customer !== c.code) fail(`Change #${req.itemId} belongs to another customer.`);
        if (["Applied", "Closed", "Review", "Refused", "Cancelled"].includes(ch!.status)) fail(`Change #${req.itemId} is finished. Use an open change.`);
        if (template.changeOnly && ch!.status !== "Accepted") fail(`Change #${req.itemId} is not approved yet: its status must be Accepted.`);
      }
      const reference = `${req.itemType} #${req.itemId}`;
      const id = state.ids.task++;
      const task: Task = {
        id, template: template.name, status: "waiting", created: iso(Date.now()), finished: null,
        message: `MSP Console, ${me().email}, ${reference}: ${reason}`.slice(0, 250), devices, by: me().name, item: reference,
        customer: c.code, link: toolUrl("semaphore", `/project/${c.projectId}/history?t=${id}`),
      };
      state.tasks.unshift(task);
      const row = audit({
        action: "playbook", customer: c.code, target: `${template.name} on ${devices.join(", ")}`, ticket: reference, reason,
        outcome: "started", result: "",
      });
      later(1500, () => {
        task.status = "running";
      });
      later(6500, () => {
        const unreachable = devices.filter((x) => state.devices.find((dv) => dv.name === x)?.host?.state === "down");
        task.status = unreachable.length ? "error" : "success";
        task.finished = iso(Date.now());
        finish(row, task.status, `Semaphore task #${id} finished with status ${task.status}${unreachable.length ? ` (${unreachable.join(", ")} unreachable)` : ""}; the result is on ${reference}.`);
      });
      return {
        message: `Semaphore task #${id} started (${template.name} on ${devices.join(", ")}). The result is added to GLPI ${reference.toLowerCase()} when it finishes.`,
        auditId: row.id, id,
      };
    },

    async createTicket(req) {
      await act();
      needEngineer("Opening tickets and changes");
      const c = customer(req.customer);
      if (!c.entityId) fail("This customer has no GLPI entity yet. Re-sync the customer first.");
      const title = req.title.trim();
      if (title.length < 5) fail("Give the title (at least 5 characters).");
      if (req.description.trim().length < 10) fail("Describe it (at least 10 characters). The description goes into GLPI as written.");
      const known = new Set(devicesOf(c.code).map((x) => x.name));
      const unknown = req.devices.filter((x) => !known.has(x));
      if (unknown.length) fail(`Not a device of ${c.name}: ${unknown.join(", ")}.`);
      if (req.kind === "Change") {
        if (!req.plannedStart || !req.plannedEnd) fail("A change needs its planned start and end.");
        if (Date.parse(req.plannedEnd!) <= Date.parse(req.plannedStart!)) fail("The planned end must be after the start.");
        if (!req.rollback || req.rollback.trim().length < 10) fail("Write the back-out plan (at least 10 characters).");
        const id = state.ids.change++;
        const category = req.template ? `Managed infrastructure > Standard change > ${req.template}` : "Managed infrastructure > Standard change";
        state.changes.unshift({
          id, name: title, status: "New", customer: c.code, category, template: req.template ?? null,
          plannedStart: req.plannedStart!, plannedEnd: req.plannedEnd!, risk: req.risk ?? "Low", devices: req.devices, requester: me().name,
          maintenanceId: null, link: toolUrl("glpi", `/front/change.form.php?id=${id}`),
        });
        const row = audit({ action: "change", customer: c.code, target: title, ticket: `Change #${id}`, reason: "", outcome: "done", result: `GLPI change #${id} created.` });
        return {
          message: `GLPI change #${id} created for ${c.name}.${req.template ? ` When it is accepted, msp-glue runs '${req.template}' on ${req.devices.join(", ") || "its devices"}.` : " It goes through approval in GLPI."}`,
          auditId: row.id, id,
        };
      }
      const id = state.ids.ticket++;
      const hours = { gold: 4, silver: 8, bronze: 24 }[c.tier];
      state.tickets.unshift({
        id, name: title, type: req.kind, status: "New", priority: req.priority, customer: c.code, opened: iso(Date.now()),
        due: iso(Date.now() + hours * HOUR), late: false, group: "NOC Level 1", technician: "", source: "MSP Console",
        device: req.devices[0] ?? null, link: toolUrl("glpi", `/front/ticket.form.php?id=${id}`),
      });
      const row = audit({ action: "ticket", customer: c.code, target: `${req.kind}: ${title}`, ticket: `Ticket #${id}`, reason: "", outcome: "done", result: `GLPI ticket #${id} created.` });
      return { message: `GLPI ticket #${id} created for ${c.name}, due in ${hours} hours under the ${c.tier} service level.`, auditId: row.id, id };
    },

    async createMaintenance(req) {
      await act();
      needEngineer("Planning maintenance");
      const c = customer(req.customer);
      const name = req.name.trim();
      if (name.length < 5) fail("Name the maintenance (at least 5 characters). Zabbix shows the name on every hidden problem.");
      const start = Date.parse(req.start);
      if (Number.isNaN(start)) fail("Give the start date and time.");
      if (start < Date.now() - 10 * MIN) fail("The start is in the past. Maintenance starts now at the earliest.");
      if (req.minutes < 15 || req.minutes > 24 * 60) fail("A maintenance window lasts 15 minutes to 24 hours.");
      if (req.devices !== null && !req.devices.length) fail("Choose the devices, or the whole customer.");
      if (c.regulated && !req.changeId) fail(`${c.name} is a regulated customer: maintenance needs a GLPI change. Choose the change it belongs to.`);
      if (req.changeId) {
        const ch = state.changes.find((x) => x.id === req.changeId && x.customer === c.code);
        if (!ch) fail(`Change #${req.changeId} is not a change of ${c.name}.`);
      }
      const id = `m-${state.ids.maintenance++}`;
      const end = start + req.minutes * MIN;
      const m: Maintenance = {
        id, name, customer: c.code, devices: req.devices, start: iso(start), end: iso(end), collectData: req.collectData,
        changeId: req.changeId, createdBy: me().name, state: "upcoming",
        link: toolUrl("zabbix", `/zabbix.php?action=maintenance.list&filter_name=${encodeURIComponent(name)}`),
      };
      state.maintenance.push(m);
      const covered = (device: string) => req.devices === null || req.devices.includes(device);
      const activate = () => {
        for (const p of state.problems) if (p.customer === c.code && covered(p.device)) p.suppressed = true;
        for (const x of devicesOf(c.code)) if (x.host && covered(x.name)) x.host.maintenance = true;
      };
      if (start <= Date.now()) activate();
      else later(Math.min(start - Date.now(), 2 ** 31 - 1), activate);
      if (req.changeId) {
        const ch = state.changes.find((x) => x.id === req.changeId);
        if (ch && !ch.maintenanceId) ch.maintenanceId = id;
      }
      const count = req.devices === null ? `every device of ${c.name}` : `${req.devices.length} device${req.devices.length === 1 ? "" : "s"}`;
      const row = audit({
        action: "maintenance", customer: c.code, target: `${req.devices === null ? "all devices" : req.devices.join(", ")}, ${req.minutes} min, data collection ${req.collectData ? "on" : "off"}`,
        ticket: req.changeId ? `Change #${req.changeId}` : "", reason: name, outcome: "done", result: `Zabbix maintenance '${name}' created.`,
      });
      return { message: `Zabbix maintenance '${name}' set for ${fmt.format(start)} to ${fmtTime.format(end)} WIB on ${count}.`, auditId: row.id, id };
    },

    async addDevice(req) {
      await act();
      needEngineer("Adding devices");
      const c = customer(req.customer);
      const name = req.name.trim().toLowerCase();
      if (!/^[a-z0-9][a-z0-9.-]{2,62}$/.test(name)) fail("Device names use lower-case letters, digits, dots and dashes, for example jkt01-sw03.");
      if (state.devices.some((x) => x.name === name)) fail(`A device called ${name} exists already.`);
      const site = c.sites.find((s) => s.id === req.site) ?? fail("Choose one of the customer's sites.");
      const ip = req.ip.trim();
      if (ip && !/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) fail("Write the address as four numbers, for example 10.23.0.12.");
      if (req.status === "Active" && !ip) fail("An active device needs its management address, so Zabbix and Golden Config can reach it.");
      if (ip && state.devices.some((x) => x.ip === ip)) fail(`${ip} is the address of another device already.`);
      const gcScope = req.status === "Active" && Boolean(ip) && req.role !== "server" && GC_PLATFORMS.has(req.platform);
      const device: Device = {
        id: `nb-${name}`, name, serial: req.serial.trim(), status: req.status, role: req.role, platform: req.platform, model: req.model.trim(),
        site: site.name, customer: c.code, ip, gcScope, monitoring: req.monitoring, driftTicket: null, host: null, check: null, backup: null,
        links: { nautobot: toolUrl("nautobot", `/dcim/devices/nb-${name}/`) },
      };
      state.devices.push(device);
      const row = audit({
        action: "add device", customer: c.code, target: `${name} (${req.status}) at ${site.name}`, ticket: "", reason: "", outcome: "done",
        result: req.status === "Active" ? "Added to Nautobot. msp-glue adds it to Zabbix, GLPI and Golden Config." : "Added to Nautobot. msp-glue provisions it once it is Active with an address.",
      });
      if (req.status === "Active" && ip && req.monitoring) {
        later(4000, () => {
          device.host = { hostid: String(10600 + state.devices.length), state: "up", error: "", maintenance: false };
          device.links.zabbix = toolUrl("zabbix", `/zabbix.php?action=latest.view&hostids%5B%5D=${device.host.hostid}`);
          if (gcScope) {
            device.check = { rules: 6, failed: [], checked: null };
            device.links.backups = toolUrl("forgejo", `/msp/backups-${c.code}/src/branch/main/${slug(site.name)}/${name}.cfg`);
          }
          refreshLinks(state);
        });
      }
      return {
        message: req.status === "Active"
          ? `${name} added to Nautobot. msp-glue adds it to Zabbix${gcScope ? ", Golden Config" : ""} and GLPI within a minute.`
          : `${name} added to Nautobot as Planned. Set it to Active with its address when it is installed.`,
        auditId: row.id, id: device.id,
      };
    },

    exportUrl: () => null,

    async setPreviewRoles(roles) {
      await wait(150);
      state.me = meFor({ ...STAFF.dimas, roles }, Date.now());
      return state.me;
    },
  };

  function backupsOf(device: Device) {
    if (!device.backup?.lastSuccess) return [];
    const last = Date.parse(device.backup.lastSuccess);
    const out = [];
    for (let i = 0; i < 6; i++) {
      const t = last - i * DAY;
      const changed = i === 0 ? 0 : i === 3 ? 4 : i === 5 ? 12 : 0;
      out.push({
        at: iso(t),
        commit: hash(`${device.name}:${i}`).toString(16).padStart(8, "0").slice(0, 7),
        changedLines: changed,
        message: changed ? `Backup of ${device.name}: ${changed} lines changed` : `Backup of ${device.name}: no change`,
        link: device.links.backups ?? toolUrl("forgejo"),
      });
    }
    return out;
  }

  return api;
}
