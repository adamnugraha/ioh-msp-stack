/* A Zabbix item over time: one measure per chart (never two y-axes), a crosshair tooltip with every
   series at that moment, the latest value at the end of the line, and a table view of the same numbers. */
import { useMemo, useState } from "react";
import type { MetricChart, TimeRange } from "@/lib/api";
import { bits, compactBits, dayLabel, duration, timeOnly, whenFull } from "@/lib/format";
import { cn } from "@/lib/utils";
import { escapeHtml, useChartTheme, type ChartTheme } from "./chart-theme";
import { EChart, type ChartOption } from "./echart";

export function formatValue(units: MetricChart["units"], v: number | null | undefined): string {
  if (v === null || v === undefined) return "no data";
  if (units === "bps") return bits(v);
  if (units === "ms") return `${v.toFixed(v < 10 ? 1 : 0)} ms`;
  if (units === "%") return `${v.toFixed(v < 10 && v > 0 ? 1 : 0)} %`;
  return String(Math.round(v));
}

function axisValue(units: MetricChart["units"], v: number): string {
  if (units === "bps") return compactBits(v);
  if (units === "%") return `${v} %`;
  if (units === "ms") return `${v} ms`;
  return String(v);
}

function latest(points: [number, number | null][]): number | null {
  for (let i = points.length - 1; i >= 0; i--) if (points[i][1] !== null) return points[i][1];
  return null;
}

/** "Now 17 ms", or how long Zabbix has had no value when the device stopped answering. */
function nowText(chart: MetricChart): string {
  const s = chart.series[0];
  const last = s.points[s.points.length - 1];
  if (!last || last[1] !== null) {
    const values = chart.series.map((x) => formatValue(chart.units, latest(x.points)));
    return chart.series.length > 1 ? `Now ${chart.series.map((x, i) => `${x.name.toLowerCase()} ${values[i]}`).join(", ")}` : `Now ${values[0]}`;
  }
  let i = s.points.length - 1;
  while (i >= 0 && s.points[i][1] === null) i--;
  if (i < 0) return "No values in this period";
  return `No value for ${duration((last[0] - s.points[i][0]) / 1000)}; the last was ${formatValue(chart.units, s.points[i][1])}`;
}

function buildOption(chart: MetricChart, range: TimeRange, t: ChartTheme, threshold?: { value: number; label: string }): ChartOption {
  const multi = chart.series.length > 1;
  const short = range === "1h" || range === "6h" || range === "24h";
  const percent = chart.units === "%";
  // percentages: a full 0 to 100 scale once values pass 60 %, otherwise a closer look
  let dataMax = 0;
  for (const s of chart.series) for (const [, v] of s.points) if (v !== null && v > dataMax) dataMax = v;
  if (threshold) dataMax = Math.max(dataMax, threshold.value);
  const percentMax = dataMax > 60 ? 100 : Math.max(10, Math.ceil((dataMax + 5) / 10) * 10);
  return {
    animation: false,
    textStyle: { fontFamily: t.font },
    grid: { left: 4, right: multi ? 92 : 64, top: 14, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: t.surface,
      borderColor: t.axis,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: t.ink, fontSize: 12, fontFamily: t.font },
      axisPointer: { type: "line", lineStyle: { color: t.axis, width: 1 } },
      formatter: (items: { axisValue: number; seriesIndex: number; value: [number, number | null] }[]) => {
        const head = `<div style="color:${t.label};margin-bottom:4px">${escapeHtml(whenFull(items[0]?.axisValue ?? 0))}</div>`;
        const rows = items
          .map((it) => {
            const s = chart.series[it.seriesIndex];
            const color = t.series[it.seriesIndex] ?? t.series[0];
            return `<div style="display:flex;align-items:center;gap:8px;line-height:1.6">
              <span style="display:inline-block;width:12px;height:2px;border-radius:1px;background:${color}"></span>
              <b style="font-weight:600">${escapeHtml(formatValue(chart.units, it.value[1]))}</b>
              <span style="color:${t.label}">${escapeHtml(s?.name ?? "")}</span></div>`;
          })
          .join("");
        return head + rows;
      },
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: t.axis } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: {
        color: t.label,
        fontSize: 11,
        hideOverlap: true,
        formatter: (v: number) => (short ? timeOnly(v) : dayLabel(v)),
      },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: percent ? percentMax : undefined,
      interval: percent && percentMax === 100 ? 25 : undefined,
      splitNumber: 4,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: t.grid, width: 1 } },
      axisLabel: { color: t.label, fontSize: 11, formatter: (v: number) => axisValue(chart.units, v) },
    },
    series: chart.series.map((s, i) => ({
      type: "line",
      name: s.name,
      data: s.points,
      showSymbol: false,
      symbolSize: 8,
      sampling: "lttb",
      connectNulls: false,
      lineStyle: { width: 2, color: t.series[i], cap: "round", join: "round" },
      itemStyle: { color: t.series[i], borderColor: t.surface, borderWidth: 2 },
      areaStyle: multi ? undefined : { color: t.series[i], opacity: 0.1 },
      emphasis: { disabled: true },
      endLabel: {
        show: true,
        color: t.ink,
        fontSize: 11.5,
        fontFamily: t.font,
        formatter: () => `${multi ? `${s.name} ` : ""}${formatValue(chart.units, latest(s.points))}`,
      },
      markLine:
        threshold && i === 0
          ? {
              symbol: "none",
              silent: true,
              lineStyle: { color: t.critical, type: "dashed", width: 1 },
              label: { color: t.label, fontSize: 11, formatter: threshold.label, position: "insideStartTop" },
              data: [{ yAxis: threshold.value }],
            }
          : undefined,
    })),
  };
}

export function Legend({ items, kind = "line" }: { items: { name: string; color: string }[]; kind?: "line" | "box" }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted-foreground">
      {items.map((it) => (
        <li key={it.name} className="inline-flex items-center gap-1.5">
          <span
            aria-hidden
            className={kind === "line" ? "h-[2px] w-3.5 rounded-full" : "size-2.5 rounded-[3px]"}
            style={{ background: it.color }}
          />
          {it.name}
        </li>
      ))}
    </ul>
  );
}

/** The chart's numbers as a table: latest, lowest, average and highest, then the values themselves. */
function SeriesTable({ chart }: { chart: MetricChart }) {
  const stats = chart.series.map((s) => {
    const values = s.points.map((p) => p[1]).filter((v): v is number => v !== null);
    const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
    return { name: s.name, latest: latest(s.points), min: values.length ? Math.min(...values) : null, avg, max: values.length ? Math.max(...values) : null, gaps: s.points.length - values.length };
  });
  const step = Math.max(1, Math.ceil(chart.series[0].points.length / 24));
  const rows = chart.series[0].points.filter((_, i) => i % step === 0 || i === chart.series[0].points.length - 1);
  return (
    <div className="space-y-3">
      <table className="w-full text-[12.5px]">
        <thead className="text-left text-muted-foreground">
          <tr>
            <th className="py-1 pr-3 font-medium">Series</th>
            <th className="py-1 pr-3 text-right font-medium">Latest</th>
            <th className="py-1 pr-3 text-right font-medium">Lowest</th>
            <th className="py-1 pr-3 text-right font-medium">Average</th>
            <th className="py-1 pr-3 text-right font-medium">Highest</th>
            <th className="py-1 text-right font-medium">No data</th>
          </tr>
        </thead>
        <tbody className="num">
          {stats.map((s) => (
            <tr key={s.name} className="border-t">
              <td className="py-1 pr-3">{s.name}</td>
              <td className="py-1 pr-3 text-right">{formatValue(chart.units, s.latest)}</td>
              <td className="py-1 pr-3 text-right">{formatValue(chart.units, s.min)}</td>
              <td className="py-1 pr-3 text-right">{formatValue(chart.units, s.avg)}</td>
              <td className="py-1 pr-3 text-right">{formatValue(chart.units, s.max)}</td>
              <td className="py-1 text-right">{s.gaps ? `${s.gaps} points` : "none"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="max-h-56 overflow-y-auto rounded border">
        <table className="w-full text-[12.5px]">
          <thead className="sticky top-0 bg-muted text-left text-muted-foreground">
            <tr>
              <th className="px-2 py-1 font-medium">Time</th>
              {chart.series.map((s) => (
                <th key={s.key} className="px-2 py-1 text-right font-medium">
                  {s.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="num">
            {rows.map((p) => {
              const index = chart.series[0].points.indexOf(p);
              return (
                <tr key={p[0]} className="border-t">
                  <td className="px-2 py-0.5">{whenFull(p[0])}</td>
                  {chart.series.map((s) => (
                    <td key={s.key} className="px-2 py-0.5 text-right">
                      {formatValue(chart.units, s.points[index]?.[1] ?? null)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function TimeSeriesCard({
  chart,
  range,
  threshold,
  dim,
  height = 190,
}: {
  chart: MetricChart;
  range: TimeRange;
  threshold?: { value: number; label: string };
  dim?: boolean;
  height?: number;
}) {
  const theme = useChartTheme();
  const [table, setTable] = useState(false);
  const option = useMemo(() => buildOption(chart, range, theme, threshold), [chart, range, theme, threshold]);
  const multi = chart.series.length > 1;
  const now = nowText(chart);
  return (
    <figure className="min-w-0 rounded-lg border bg-card p-3.5">
      <figcaption className="mb-2 flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <div className="text-[13.5px] font-semibold">{chart.title}</div>
          <div className="text-[12.5px] text-muted-foreground">{now}</div>
        </div>
        <div className="flex items-center gap-3">
          {multi && <Legend items={chart.series.map((s, i) => ({ name: s.name, color: theme.series[i] }))} />}
          <button
            type="button"
            onClick={() => setTable((v) => !v)}
            aria-pressed={table}
            className={cn("rounded-md border px-2 py-0.5 text-[12px] text-muted-foreground hover:bg-accent hover:text-foreground", table && "bg-accent text-foreground")}
          >
            {table ? "Chart" : "Table"}
          </button>
        </div>
      </figcaption>
      {table ? (
        <SeriesTable chart={chart} />
      ) : (
        <EChart option={option} height={height} dim={dim} label={`${chart.title}: ${now}`} />
      )}
    </figure>
  );
}
