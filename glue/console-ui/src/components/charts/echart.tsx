/* ECharts, with only the parts the console uses (line and bar charts, grid, tooltip, reference lines). */
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);

export type ChartOption = echarts.EChartsCoreOption;

export function EChart({
  option,
  height,
  className,
  label,
  dim,
}: {
  option: ChartOption;
  height: number;
  className?: string;
  /** what the chart shows, for screen readers (the table view has the values) */
  label: string;
  /** a new answer is loading: keep the old picture, slightly faded */
  dim?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const instance = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  return (
    <div
      ref={ref}
      role="img"
      aria-label={label}
      style={{ height }}
      className={cn("w-full transition-opacity", dim && "opacity-60", className)}
    />
  );
}
