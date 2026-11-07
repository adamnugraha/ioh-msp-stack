/* Monthly customer reports (kit 1.6.0): the console drafts last month's report for every customer, an engineer
   or manager checks the text against the figures and approves it, then prints it for the customer. */
import { CheckCheck, FileText, Printer, RefreshCw, RotateCcw, Save, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";
import { api, DEMO, type ReportDetail, type ReportFlag, type ReportNarrative, type ReportStatus } from "@/lib/api";
import { ago, pct } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { errorText, useAction, useMe, useReport, useReports } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Empty, ErrorBlock, LoadingBlock, Page, PageHeader, Section, Stat, TableWrap } from "@/components/page";
import { Pill } from "@/components/status";
import type { Tone } from "@/lib/status";

const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-3 py-2.5 align-top";

const STATUS: Record<ReportStatus, { text: string; tone: Tone }> = {
  none: { text: "not drafted", tone: "neutral" },
  drafting: { text: "drafting", tone: "info" },
  draft: { text: "draft, to check", tone: "warn" },
  approved: { text: "approved", tone: "ok" },
  failed: { text: "failed", tone: "bad" },
};
const LANGUAGE = { id: "Bahasa Indonesia", en: "English" };
const SECTIONS: { key: Exclude<keyof ReportNarrative, "recommendations">; label: string; hint: string }[] = [
  { key: "summary", label: "Summary", hint: "Three or four sentences on the month as a whole" },
  { key: "availability", label: "Availability", hint: "Achieved against target, downtime, the longest problems" },
  { key: "service", label: "Tickets and service levels", hint: "Tickets opened and solved, service levels missed" },
  { key: "paths", label: "Path quality", hint: "Path tests from the customer's sites; empty when it has none" },
  { key: "capacity", label: "Link capacity", hint: "Links close to their capacity; empty when there are none" },
];

const useCanReport = () => {
  const { data: me } = useMe();
  return Boolean(me?.canAct || me?.isManager);
};

function openPrint(code: string, month: string) {
  const url = api.reportPrintUrl(code, month);
  if (!url || DEMO) toast("Opens the printable report", { description: "A page in the customer's language, ready to save as PDF from the browser's print menu. Not available in the preview." });
  else window.open(url, "_blank", "noopener");
}

// ======================================================================================= list
export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const month = params.get("month") ?? undefined;
  const { data, error, isLoading, isFetching } = useReports(month);
  const canReport = useCanReport();
  const now = useNow(30_000);
  const draft = useAction(({ code, month }: { code: string; month: string }) => api.draftReport(code, month));
  if (isLoading) return <LoadingBlock />;
  if (error || !data) return <ErrorBlock error={error} />;
  const counts = { approved: 0, draft: 0, none: 0 };
  for (const r of data.rows) {
    if (r.status === "approved") counts.approved++;
    else if (r.status === "draft") counts.draft++;
    else counts.none++;
  }
  const flagged = data.rows.reduce((n, r) => n + (r.status === "draft" ? r.flags : 0), 0);
  return (
    <Page>
      <PageHeader
        title="Monthly reports"
        meta={
          <span>
            Drafted on the first night of each month from Zabbix, GLPI, Golden Config, path tests and the capacity forecast.{" "}
            {data.writer.model ? <>Text written by {data.writer.model}, checked by an engineer.</> : <>No model is set (CONSOLE_AI_URL), so drafts use fixed sentences.</>}
          </span>
        }
        actions={
          <Select value={data.month} onValueChange={(v) => setParams({ month: v })}>
            <SelectTrigger size="sm" className="w-48" aria-label="Month">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {data.monthsAvailable.map((m) => (
                <SelectItem key={m.month} value={m.month}>{m.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      <div className={cn("space-y-5 transition-opacity", isFetching && "opacity-80")}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Approved" value={`${counts.approved} of ${data.rows.length}`} tone={counts.approved === data.rows.length ? "ok" : undefined} note={data.label} />
          <Stat label="Drafts to check" value={counts.draft} tone={counts.draft ? "warn" : "ok"} note="Read, correct if needed, approve" />
          <Stat label="Numbers to check" value={flagged} tone={flagged ? "warn" : "ok"} note="In drafts: numbers the data does not contain" />
          <Stat label="Not drafted" value={counts.none} note="Draft them now, or wait for the night run" />
        </div>
        <Section title={`Reports for ${data.label}`}>
          <TableWrap>
            <table className="w-full min-w-[820px] text-[13px]">
              <thead className="border-b bg-muted/40">
                <tr>
                  <th className={th}>Customer</th>
                  <th className={th}>Language</th>
                  <th className={th}>Status</th>
                  <th className={th}>Numbers to check</th>
                  <th className={th}>Last change</th>
                  <th className={th} />
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map((r) => (
                  <tr key={r.code}>
                    <td className={td}>
                      <Link to={`/reports/${r.code}/${data.month}`} className="font-semibold hover:underline">{r.name}</Link>
                      <div className="text-[12px] text-muted-foreground">{r.code}</div>
                    </td>
                    <td className={td}>{LANGUAGE[r.language]}</td>
                    <td className={td}>
                      <Pill tone={STATUS[r.status].tone} spin={r.status === "drafting"}>{STATUS[r.status].text}</Pill>
                      {r.status === "approved" && r.approvedBy && <div className="mt-0.5 text-[12px] text-muted-foreground">by {r.approvedBy}</div>}
                      {r.error && <div className="mt-0.5 max-w-[300px] text-[12px] text-warn">{r.error}</div>}
                    </td>
                    <td className={cn(td, "num")}>{r.status === "draft" || r.status === "approved" ? (r.flags ? <span className="text-warn">{r.flags}</span> : "none") : ""}</td>
                    <td className={cn(td, "whitespace-nowrap")}>{r.updated ? ago(r.updated, now) : ""}</td>
                    <td className={cn(td, "whitespace-nowrap text-right")}>
                      {canReport && ["none", "failed"].includes(r.status) && (
                        <Button size="sm" variant="outline" disabled={draft.isPending} onClick={() => draft.mutate({ code: r.code, month: data.month }, { onError: (e) => toast.error(errorText(e)) })}>
                          <FileText /> Draft now
                        </Button>
                      )}
                      {["draft", "approved"].includes(r.status) && (
                        <Button size="sm" variant="ghost" asChild>
                          <Link to={`/reports/${r.code}/${data.month}`}>Open</Link>
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Section>
      </div>
    </Page>
  );
}

// ===================================================================================== one report
function FlagBanner({ flags }: { flags: ReportFlag[] }) {
  if (!flags.length) return null;
  const by = new Map<string, string[]>();
  for (const f of flags) by.set(f.section, [...(by.get(f.section) ?? []), f.number]);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-bg px-4 py-3 text-[13px] text-warn">
      <TriangleAlert className="mt-0.5 size-4 shrink-0" />
      <div>
        <b className="font-semibold">Check these numbers before approving:</b> they are in the text but not in the data the report was built from.
        <ul className="mt-1 list-disc pl-5">
          {[...by].map(([section, numbers]) => (
            <li key={section}>
              {SECTIONS.find((s) => s.key === section)?.label ?? "Recommendations"}: {numbers.join(", ")}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Figures({ r }: { r: ReportDetail }) {
  const f = r.facts!;
  const a = f.availability;
  const t = f.tickets;
  const longest = "longest" in f.problems ? f.problems.longest : [];
  const paths = Array.isArray(f.paths) ? f.paths : [];
  const capacity = Array.isArray(f.capacity) ? f.capacity : [];
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Availability" value={a.achieved_percent != null ? pct(a.achieved_percent, 3) : "none"} tone={a.met === false ? "bad" : "ok"}
          note={a.target_percent != null ? `Target ${a.target_percent} %, ${a.downtime_minutes ?? 0} minutes down` : "No agreement yet"} />
        <Stat label="Tickets" value={`${t.opened} opened`} note={`${t.solved} solved, ${t.service_level_breaches} missed the service level`} tone={t.service_level_breaches ? "warn" : "ok"} />
        <Stat label="Mean time to resolve" value={t.mean_hours_to_resolve != null ? `${t.mean_hours_to_resolve} h` : "none"} note="Solved tickets of the month" />
        <Stat label="Configuration" value={f.configuration.compliant_percent != null ? `${f.configuration.compliant_percent} %` : "none"}
          note={`compliant, ${f.configuration.backups_fresh_percent ?? "-"} % backed up, at the time of the report`} />
      </div>
      {(longest.length > 0 || paths.length > 0 || capacity.length > 0) && (
        <Section title="Tables of the report" description="Printed from the data, never from the model's text">
          <div className="space-y-4 p-4 text-[13px]">
            {longest.length > 0 && (
              <TableWrap>
                <table className="w-full min-w-[600px]">
                  <caption className="mb-1 text-left text-[12.5px] font-medium text-muted-foreground">Longest problems</caption>
                  <tbody className="divide-y">
                    {longest.map((p) => (
                      <tr key={p.started + p.device}><td className="py-1.5 pr-3 font-medium">{p.device}</td><td className="py-1.5 pr-3">{p.problem}</td>
                        <td className="py-1.5 pr-3 whitespace-nowrap">{p.started.replace("T", " ")}</td><td className="num py-1.5">{p.minutes} min</td></tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
            {paths.length > 0 && (
              <TableWrap>
                <table className="w-full min-w-[600px]">
                  <caption className="mb-1 text-left text-[12.5px] font-medium text-muted-foreground">Path tests, monthly averages</caption>
                  <tbody className="divide-y">
                    {paths.map((p) => (
                      <tr key={p.collector + p.target}><td className="py-1.5 pr-3">{p.collector}</td><td className="py-1.5 pr-3 font-medium">{p.target}</td>
                        <td className="num py-1.5 pr-3">{p.delay_ms != null ? `${p.delay_ms} ms delay` : p.web_ms != null ? `${p.web_ms} ms web` : ""}</td>
                        <td className="num py-1.5 pr-3">{p.loss_percent != null ? `${p.loss_percent} % loss` : ""}</td>
                        <td className="py-1.5">{p.problems ? <span className="text-warn">{p.problems} problems</span> : "no problem"}</td></tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
            {capacity.length > 0 && (
              <TableWrap>
                <table className="w-full min-w-[600px]">
                  <caption className="mb-1 text-left text-[12.5px] font-medium text-muted-foreground">Link capacity</caption>
                  <tbody className="divide-y">
                    {capacity.map((c) => (
                      <tr key={c.device + c.interface}><td className="py-1.5 pr-3 font-medium">{c.device} {c.interface}</td><td className="py-1.5 pr-3">{c.circuit}</td>
                        <td className="num py-1.5 pr-3">{c.busy_hour_mbps} of {c.capacity_mbps} Mbit/s</td>
                        <td className="num py-1.5">{c.utilisation_percent} %{c.days_to_warning ? `, warning level in ${Math.round(c.days_to_warning)} days` : ""}</td></tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </div>
        </Section>
      )}
    </div>
  );
}

export function ReportPage() {
  const { code = "", month = "" } = useParams();
  const { data: r, error, isLoading } = useReport(code, month);
  const canReport = useCanReport();
  const now = useNow(30_000);
  const [text, setText] = useState<ReportNarrative | null>(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (r?.narrative && !dirty) setText(r.narrative);
  }, [r?.narrative, r?.updated, dirty]);
  const draft = useAction(() => api.draftReport(code, month));
  const save = useAction((t: ReportNarrative) => api.saveReport(code, month, t));
  const approve = useAction(() => api.approveReport(code, month));
  const reopen = useAction(() => api.reopenReport(code, month));
  if (isLoading) return <LoadingBlock />;
  if (error || !r) return <ErrorBlock error={error} />;
  const editable = canReport && r.status === "draft";
  const onError = (e: unknown) => toast.error(errorText(e));
  const set = (key: keyof ReportNarrative, value: string) => {
    setDirty(true);
    setText((t) => ({ ...(t as ReportNarrative), [key]: key === "recommendations" ? value.split("\n") : value }));
  };
  const clean = (t: ReportNarrative): ReportNarrative => ({ ...t, recommendations: t.recommendations.map((x) => x.trim()).filter(Boolean) });
  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Monthly reports", to: `/reports?month=${month}` }, { label: r.label }]}
        title={r.name}
        badge={<Pill tone={STATUS[r.status].tone} spin={r.status === "drafting"}>{STATUS[r.status].text}</Pill>}
        meta={
          r.status === "none" ? undefined : (
            <>
              {r.language && <span>{LANGUAGE[r.language]}</span>}
              {r.writer && <span>Text by {r.writer}</span>}
              {r.updated && <span>Changed {ago(r.updated, now)}{r.editedBy ? ` by ${r.editedBy}` : ""}</span>}
              {r.approvedBy && <span>Approved by {r.approvedBy}{r.approvedAt ? `, ${ago(r.approvedAt, now)}` : ""}</span>}
            </>
          )
        }
        actions={
          <>
            {["draft", "approved"].includes(r.status) && (
              <Button variant="outline" size="sm" onClick={() => openPrint(code, month)}>
                <Printer /> Printable page
              </Button>
            )}
            {canReport && ["none", "failed", "draft"].includes(r.status) && (
              <Button variant="outline" size="sm" disabled={draft.isPending} onClick={() => { setDirty(false); draft.mutate(undefined, { onError }); }}>
                <RefreshCw /> {r.status === "none" ? "Draft now" : "Draft again"}
              </Button>
            )}
            {canReport && r.status === "draft" && (
              <Button size="sm" disabled={approve.isPending || dirty} title={dirty ? "Save the text first" : undefined} onClick={() => approve.mutate(undefined, { onError })}>
                <CheckCheck /> Approve
              </Button>
            )}
            {canReport && r.status === "approved" && (
              <Button variant="outline" size="sm" disabled={reopen.isPending} onClick={() => reopen.mutate(undefined, { onError })}>
                <RotateCcw /> Reopen
              </Button>
            )}
          </>
        }
      />
      {r.status === "none" && (
        <Section title="No draft yet">
          <Empty>
            The console drafts last month's reports on the first night of the month. {canReport ? "Draft this one now with the button above." : "An engineer or manager can draft it now."}
          </Empty>
        </Section>
      )}
      {r.status === "drafting" && <LoadingBlock label="Drafting: collecting the month's figures and writing the text (a few minutes with a model)" />}
      {r.status === "failed" && <ErrorBlock error={new Error(`The draft failed: ${r.error || "see the console's log"}`)} />}
      {(r.status === "draft" || r.status === "approved") && r.facts && text && (
        <div className="space-y-5">
          {r.error && <div className="rounded-lg border border-warn/30 bg-warn-bg px-4 py-3 text-[13px] text-warn">{r.error}</div>}
          {r.status === "draft" && <FlagBanner flags={r.flags ?? []} />}
          <Figures r={r} />
          <Section
            title="Text of the report"
            description={editable ? "Correct anything that does not match the figures, save, then approve." : r.status === "approved" ? "Approved: reopen the report to change the text." : undefined}
            actions={editable && (
              <Button size="sm" variant="outline" disabled={!dirty || save.isPending}
                onClick={() => save.mutate(clean(text), { onSuccess: () => setDirty(false), onError })}>
                <Save /> Save text
              </Button>
            )}
          >
            <div className="space-y-4 p-4">
              {SECTIONS.map((s) => (
                <label key={s.key} className="block">
                  <span className="text-[13px] font-semibold">{s.label}</span>
                  <span className="ml-2 text-[12px] text-muted-foreground">{s.hint}</span>
                  <Textarea className="mt-1 min-h-[72px] text-[13.5px] leading-relaxed" value={text[s.key]} readOnly={!editable}
                    onChange={(e) => set(s.key, e.target.value)} />
                </label>
              ))}
              <label className="block">
                <span className="text-[13px] font-semibold">Recommendations</span>
                <span className="ml-2 text-[12px] text-muted-foreground">One per line, at most five</span>
                <Textarea className="mt-1 min-h-[72px] text-[13.5px] leading-relaxed" value={text.recommendations.join("\n")} readOnly={!editable}
                  onChange={(e) => set("recommendations", e.target.value)} />
              </label>
            </div>
          </Section>
        </div>
      )}
    </Page>
  );
}
