/* Path tests from the customer's site collectors (kit 1.6.0): for each collector, which side of the path a
   slowdown is on, and every target's latest values against its limits. */
import { CircleAlert } from "lucide-react";
import type { PathCollector, PathSegment, PathState, PathTarget } from "@/lib/api";
import { ago } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { usePaths } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { Empty, ErrorBlock, LoadingBlock, Section, TableWrap } from "@/components/page";
import { Pill } from "@/components/status";
import { MiniTrend } from "@/components/trend";
import type { Tone } from "@/lib/status";
import { useCustomerData } from "./layout";

const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-3 py-2.5 align-top";

const SEGMENT: Record<PathSegment, { label: string; hint: string }> = {
  ioh: { label: "IOH's edge", hint: "IOH's router at the site: tells IOH's access link apart from the rest of the way" },
  internet: { label: "Internet", hint: "Public reference points beyond IOH's edge" },
  application: { label: "Application", hint: "What the customer uses" },
};

const STATE: Record<PathState | "partial", { text: string; tone: Tone }> = {
  ok: { text: "fine", tone: "ok" },
  warn: { text: "slow or lossy", tone: "warn" },
  bad: { text: "not answering", tone: "bad" },
  info: { text: "above its usual level", tone: "info" },
  unknown: { text: "no data yet", tone: "neutral" },
  partial: { text: "some points only, so not the path", tone: "neutral" },
};

const ms = (v: number | null) => (v === null ? "" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`);

function Now({ t }: { t: PathTarget }) {
  const rows: string[] = [];
  if (t.delayMs !== null) rows.push(`delay ${ms(t.delayMs)}`);
  if (t.lossPct !== null) rows.push(`loss ${t.lossPct} %`);
  if (t.connectMs !== null) rows.push(`connection ${ms(t.connectMs)}`);
  if (t.webMs !== null) rows.push(`web ${ms(t.webMs)}${t.webStatus ? `, HTTP ${t.webStatus}` : ""}`);
  return (
    <div className="space-y-0.5 whitespace-nowrap">
      {rows.length ? rows.map((r) => <div key={r} className="num">{r}</div>) : <span className="text-muted-foreground">none yet</span>}
      {t.webError && <div className="max-w-[260px] whitespace-normal text-[12px] text-bad">{t.webError}</div>}
    </div>
  );
}

function Collector({ c, now }: { c: PathCollector; now: number }) {
  const tone = c.verdict.tone;
  return (
    <Section
      title={<>From {c.name}</>}
      description={c.lastSeen ? `Collector last heard ${ago(c.lastSeen, now)}` : "The collector has not connected yet"}
      actions={<Pill tone={c.online ? "ok" : "bad"}>{c.online ? "online" : "offline"}</Pill>}
    >
      <div
        className={cn(
          "flex items-start gap-2.5 border-b px-4 py-3 text-[13.5px]",
          tone === "bad" && "bg-bad-bg text-bad",
          tone === "warn" && "bg-warn-bg text-warn",
          tone === "ok" && "bg-ok-bg/50",
        )}
        role="status"
      >
        <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        <div>
          <b className="font-semibold">{c.verdict.side === "none" ? "Where the path stands" : "Where the trouble is"}:</b> {c.verdict.text}
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {(Object.keys(SEGMENT) as PathSegment[]).filter((s) => c.segments[s]).map((s) => (
              <Pill key={s} tone={STATE[c.segments[s]!].tone} icon={null} title={SEGMENT[s].hint}>
                {SEGMENT[s].label}: {STATE[c.segments[s]!].text}
              </Pill>
            ))}
          </div>
        </div>
      </div>
      <TableWrap>
        <table className="w-full min-w-[900px] text-[13px]">
          <thead className="border-b bg-muted/40">
            <tr>
              <th className={th}>Target</th>
              <th className={th}>Segment</th>
              <th className={th}>Now</th>
              <th className={th}>Last 24 hours</th>
              <th className={th}>State</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {c.targets.map((t) => {
              const state = STATE[t.state];
              const limit = t.historyKind === "web" ? t.limits.webMs : t.limits.delayMs;
              return (
                <tr key={t.name}>
                  <td className={td}>
                    <div className="font-semibold">{t.name}</div>
                    <div className="max-w-[320px] truncate font-mono text-[12px] text-muted-foreground" title={t.url || t.address}>
                      {t.url || t.address}
                    </div>
                    <div className="text-[12px] text-muted-foreground">{t.checks.map((x) => ({ icmp: "ping", tcp: "connection", https: "web request" })[x]).join(", ")}</div>
                  </td>
                  <td className={td} title={SEGMENT[t.segment]?.hint}>{SEGMENT[t.segment]?.label ?? t.segment}</td>
                  <td className={td}><Now t={t} /></td>
                  <td className={td}>
                    <MiniTrend
                      points={t.history}
                      limit={limit}
                      tone={state.tone}
                      label={`${t.historyKind === "web" ? "Web response time" : t.historyKind === "connect" ? "Connection time" : "Delay"} to ${t.name}, hourly averages`}
                    />
                    {limit !== null && <div className="text-[12px] text-muted-foreground">limit {ms(limit)}</div>}
                  </td>
                  <td className={td}>
                    <Pill tone={state.tone}>{state.text}</Pill>
                    {t.problems.map((p) => (
                      <div key={p.name} className="mt-1 max-w-[280px] text-[12px]">
                        {p.name} <span className="text-muted-foreground">({ago(p.since, now)})</span>
                      </div>
                    ))}
                    {t.broken.map((b) => <div key={b} className="mt-1 max-w-[280px] text-[12px] text-bad">Zabbix cannot run it: {b}</div>)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
    </Section>
  );
}

export function CustomerPaths() {
  const data = useCustomerData();
  const now = useNow(30_000);
  const { data: view, error, isLoading } = usePaths(data.summary.code);
  if (isLoading) return <LoadingBlock />;
  if (error || !view) return <ErrorBlock error={error} />;
  const howTo = (
    <p className="px-4 py-3 text-[12.5px] leading-relaxed text-muted-foreground">
      Every collector tests the targets in <span className="font-mono">glue/mappings.yaml</span> (path_tests) and the customer's own in the
      Nautobot tenant field <span className="font-mono">msp_path_targets</span>, for example IOH's edge router at the site. msp-glue keeps
      one Zabbix host per collector and target; these problems never open tickets and never count as downtime.
    </p>
  );
  return (
    <div className="space-y-5">
      {view.errors.length > 0 && (
        <div className="rounded-lg border border-warn/30 bg-warn-bg px-4 py-3 text-[13px] text-warn">
          <b className="font-semibold">Targets not used:</b>
          <ul className="mt-1 list-disc pl-5">{view.errors.map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
      )}
      {!view.hasCollector ? (
        <Section title="Path tests" description="Delay, loss and response time from the customer's own sites">
          <Empty>
            This customer has no site collector, so there is nothing to test from its sites. Register one with{" "}
            <span className="font-mono">mspctl proxy add {data.summary.code} &lt;name&gt;</span>, set the tenant field{" "}
            <span className="font-mono">msp_proxy_group</span> in Nautobot, and the tests start within ten minutes.
          </Empty>
          {howTo}
        </Section>
      ) : !view.collectors.length ? (
        <Section title="Path tests">
          <Empty>The customer's collectors have no path test hosts yet. msp-glue creates them at its next check (within ten minutes).</Empty>
          {howTo}
        </Section>
      ) : (
        <>
          {view.collectors.map((c) => <Collector key={c.name} c={c} now={now} />)}
          <div className="rounded-lg border bg-card">{howTo}</div>
        </>
      )}
    </div>
  );
}
