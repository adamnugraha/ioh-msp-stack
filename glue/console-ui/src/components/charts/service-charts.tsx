/* Charts for availability and service levels: downtime per day, error budget per customer,
   a six-month trend for table rows, and the budget meter. */
import { useMemo, useState } from "react";
import { Link } from "react-router";
import type { ServiceRow, Sla } from "@/lib/api";
import { duration, pct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { escapeHtml, useChartTheme } from "./chart-theme";
import { EChart, type ChartOption } from "./echart";

function toggleClass(on: boolean) {
  return cn("rounded-md border px-2 py-0.5 text-[12px] text-muted-foreground hover:bg-accent hover:text-foreground", on && "bg-accent text-foreground");
}

/** Downtime minutes per day this month (only the agreement's downtime: availability problems of High or worse). */
export function DowntimeColumns({ days, title = "Downtime per day this month" }: { days: { day: string; minutes: number }[]; title?: string }) {
  const t = useChartTheme();
  const [table, setTable] = useState(false);
  const total = days.reduce((s, d) => s + d.minutes, 0);
  const option = useMemo<ChartOption>(() => ({
    animation: false,
    textStyle: { fontFamily: t.font },
    grid: { left: 4, right: 8, top: 22, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: t.surface,
      borderColor: t.axis,
      textStyle: { color: t.ink, fontSize: 12, fontFamily: t.font },
      formatter: (p: { name: string; value: number }) =>
        `<b style="font-weight:600">${p.value ? escapeHtml(duration(p.value * 60)) : "No downtime"}</b><div style="color:${t.label}">${escapeHtml(p.name)}</div>`,
    },
    xAxis: {
      type: "category",
      data: days.map((d) => d.day),
      axisLine: { lineStyle: { color: t.axis } },
      axisTick: { show: false },
      axisLabel: { color: t.label, fontSize: 11, formatter: (v: string) => String(Number(v.slice(8))), interval: days.length > 20 ? 2 : 0 },
    },
    yAxis: {
      type: "value",
      minInterval: 10,
      splitNumber: 3,
      axisLabel: { color: t.label, fontSize: 11, formatter: (v: number) => `${v} min` },
      splitLine: { lineStyle: { color: t.grid } },
    },
    series: [{
      type: "bar",
      data: days.map((d) => d.minutes),
      barMaxWidth: 18,
      itemStyle: { color: t.series[0], borderRadius: [4, 4, 0, 0] },
      emphasis: { itemStyle: { opacity: 0.85 } },
      label: { show: true, position: "top", color: t.ink, fontSize: 11, formatter: (p: { value: number }) => (p.value ? `${p.value}` : "") },
    }],
  }), [days, t]);
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 flex items-start justify-between gap-3">
        <div>
          <div className="text-[13.5px] font-semibold">{title}</div>
          <div className="text-[12.5px] text-muted-foreground">{total ? `${duration(total * 60)} in total, minutes per day` : "No downtime this month"}</div>
        </div>
        <button type="button" className={toggleClass(table)} aria-pressed={table} onClick={() => setTable((v) => !v)}>
          {table ? "Chart" : "Table"}
        </button>
      </figcaption>
      {table ? (
        <div className="max-h-52 overflow-y-auto rounded border">
          <table className="w-full text-[12.5px]">
            <thead className="sticky top-0 bg-muted text-left text-muted-foreground">
              <tr><th className="px-2 py-1 font-medium">Day</th><th className="px-2 py-1 text-right font-medium">Downtime</th></tr>
            </thead>
            <tbody className="num">
              {days.map((d) => (
                <tr key={d.day} className="border-t"><td className="px-2 py-0.5">{d.day}</td><td className="px-2 py-0.5 text-right">{d.minutes ? duration(d.minutes * 60) : "none"}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EChart option={option} height={170} label={`${title}: ${total} minutes in total`} />
      )}
    </figure>
  );
}

/** Error budget used this month per customer: 100 % means the month's allowed downtime is spent. */
export function BudgetBars({ rows }: { rows: ServiceRow[] }) {
  const t = useChartTheme();
  const [table, setTable] = useState(false);
  const data = rows
    .filter((r) => r.sla)
    .map((r) => ({ name: r.name, code: r.code, used: r.sla!.allowed ? (100 * r.sla!.downtime) / r.sla!.allowed : 0, sla: r.sla! }))
    .sort((a, b) => b.used - a.used);
  const max = Math.max(125, ...data.map((d) => Math.ceil((d.used + 10) / 25) * 25));
  const option = useMemo<ChartOption>(() => ({
    animation: false,
    textStyle: { fontFamily: t.font },
    grid: { left: 4, right: 64, top: 22, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: t.surface,
      borderColor: t.axis,
      textStyle: { color: t.ink, fontSize: 12, fontFamily: t.font },
      formatter: (p: { dataIndex: number }) => {
        const d = data[p.dataIndex];
        return `<b style="font-weight:600">${d.used.toFixed(0)} % of the budget</b>
          <div>${escapeHtml(d.name)}</div>
          <div style="color:${t.label}">Down ${escapeHtml(duration(d.sla.downtime))} of ${escapeHtml(duration(d.sla.allowed))} allowed for the month (target ${d.sla.slo} %)</div>`;
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max,
      interval: 25,
      axisLabel: { color: t.label, fontSize: 11, formatter: (v: number) => `${v} %` },
      splitLine: { lineStyle: { color: t.grid } },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: data.map((d) => d.name),
      axisLine: { lineStyle: { color: t.axis } },
      axisTick: { show: false },
      axisLabel: { color: t.ink, fontSize: 12 },
    },
    series: [{
      type: "bar",
      barMaxWidth: 18,
      data: data.map((d) => ({ value: +d.used.toFixed(1), itemStyle: { color: d.used > 100 ? t.critical : t.series[0], borderRadius: [0, 4, 4, 0] } })),
      label: {
        show: true,
        position: "right",
        color: t.ink,
        fontSize: 11.5,
        formatter: (p: { value: number }) => `${Math.round(p.value)} %${p.value > 100 ? " over" : ""}`,
      },
      markLine: {
        symbol: "none",
        silent: true,
        lineStyle: { color: t.ink, type: "solid", width: 1, opacity: 0.55 },
        label: { color: t.label, fontSize: 11, formatter: "Budget", position: "start" },
        data: [{ xAxis: 100 }],
      },
    }],
  }), [data, max, t]);
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 flex items-start justify-between gap-3">
        <div>
          <div className="text-[13.5px] font-semibold">Error budget used this month</div>
          <div className="text-[12.5px] text-muted-foreground">Downtime as a share of what each customer's target allows for the whole month</div>
        </div>
        <button type="button" className={toggleClass(table)} aria-pressed={table} onClick={() => setTable((v) => !v)}>
          {table ? "Chart" : "Table"}
        </button>
      </figcaption>
      {table ? (
        <table className="w-full text-[12.5px]">
          <thead className="text-left text-muted-foreground">
            <tr><th className="py-1 pr-2 font-medium">Customer</th><th className="py-1 pr-2 text-right font-medium">Used</th><th className="py-1 pr-2 text-right font-medium">Down</th><th className="py-1 text-right font-medium">Allowed</th></tr>
          </thead>
          <tbody className="num">
            {data.map((d) => (
              <tr key={d.code} className="border-t">
                <td className="py-1 pr-2"><Link className="hover:underline" to={`/customers/${d.code}`}>{d.name}</Link></td>
                <td className="py-1 pr-2 text-right">{d.used.toFixed(0)} %</td>
                <td className="py-1 pr-2 text-right">{duration(d.sla.downtime)}</td>
                <td className="py-1 text-right">{duration(d.sla.allowed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EChart option={option} height={Math.max(150, data.length * 34 + 40)} label="Error budget used this month per customer" />
      )}
    </figure>
  );
}

/** Six months of availability in a table cell. Grey line, this month as a dot, the target as a hairline. */
export function Sparkline({ values, target, className }: { values: (number | null)[]; target: number | null; className?: string }) {
  const w = 96;
  const h = 26;
  const nums = values.filter((v): v is number => v !== null);
  if (nums.length < 2) return <span className="text-[12px] text-muted-foreground">not enough data</span>;
  const lo = Math.min(...nums, target ?? 100) - 0.05;
  const hi = 100;
  const x = (i: number) => 3 + (i * (w - 6)) / (values.length - 1);
  const y = (v: number) => 3 + ((hi - v) * (h - 6)) / Math.max(0.001, hi - lo);
  const points = values.map((v, i) => (v === null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean).join(" ");
  const last = values[values.length - 1];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className={className} aria-hidden>
      {target !== null && <line x1={2} x2={w - 2} y1={y(target)} y2={y(target)} stroke="var(--chart-axis)" strokeWidth={1} />}
      <polyline points={points} fill="none" stroke="var(--chart-muted)" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      {last !== null && (
        <circle cx={x(values.length - 1)} cy={y(last)} r={3.5} fill={target !== null && last < target ? "var(--status-critical)" : "var(--series-1)"} stroke="var(--card)" strokeWidth={1.5} />
      )}
    </svg>
  );
}

/** How much of the month's allowed downtime is spent. */
export function BudgetMeter({ sla, className, compact }: { sla: Sla; className?: string; compact?: boolean }) {
  const used = sla.allowed ? (100 * sla.downtime) / sla.allowed : 0;
  const fill = used > 100 ? "var(--status-critical)" : used > 75 ? "var(--status-warning)" : "var(--series-1)";
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="h-2 w-full overflow-hidden rounded-full" style={{ background: "var(--meter-track)" }} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(used, 100))} aria-label="Error budget used">
        <div className="h-full rounded-full" style={{ width: `${Math.min(100, used)}%`, background: fill }} />
      </div>
      {!compact && <div className="text-[12.5px] text-muted-foreground">
        {sla.downtime === 0
          ? `No downtime this month. The ${pct(sla.slo, 1)} target allows ${duration(sla.allowed)}.`
          : `${Math.round(used)} % of the month's budget used: down ${duration(sla.downtime)} of ${duration(sla.allowed)} allowed at ${pct(sla.slo, 1)}${used > 100 ? ". Over budget." : "."}`}
      </div>}
    </div>
  );
}
