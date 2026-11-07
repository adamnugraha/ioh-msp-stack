/* A small line of recent values, for tables: path delay over 24 hours, a link's busy hour over four weeks.
   An optional limit draws as a dashed line, and the last point is a dot in the row's tone. */
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/status";

const DOT: Record<Tone, string> = {
  ok: "var(--status-good)",
  warn: "var(--status-warning)",
  bad: "var(--status-critical)",
  info: "var(--series-1)",
  neutral: "var(--chart-muted)",
};

export function MiniTrend({
  points,
  limit,
  tone = "neutral",
  width = 120,
  height = 28,
  label,
  className,
}: {
  points: [number, number][];
  limit?: number | null;
  tone?: Tone;
  width?: number;
  height?: number;
  label: string;
  className?: string;
}) {
  if (points.length < 2) return <span className="text-[12px] text-muted-foreground">no data yet</span>;
  const values = points.map((p) => p[1]);
  const top = Math.max(...values, limit ?? 0) * 1.08 || 1;
  const first = points[0][0];
  const span = points[points.length - 1][0] - first || 1;
  const x = (t: number) => 2 + ((t - first) / span) * (width - 6);
  const y = (v: number) => height - 2 - (v / top) * (height - 4);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className={cn("block", className)}>
      {limit != null && limit < top && (
        <line x1={2} x2={width - 2} y1={y(limit)} y2={y(limit)} stroke="var(--chart-muted)" strokeDasharray="3 3" strokeWidth={1} />
      )}
      <path d={line} fill="none" stroke="var(--series-1)" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last[0])} cy={y(last[1])} r={2.6} fill={DOT[tone]} />
    </svg>
  );
}
