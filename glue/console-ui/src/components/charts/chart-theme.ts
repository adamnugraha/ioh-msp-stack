/* Chart colours come from the CSS tokens (index.css), so light and dark each use their own validated
   steps. Charts re-render when the theme changes. */
import { useEffect, useState } from "react";
import { useTheme } from "@/lib/theme";

export interface ChartTheme {
  surface: string;
  grid: string;
  axis: string;
  label: string;
  ink: string;
  muted: string;
  series: [string, string, string];
  good: string;
  warning: string;
  serious: string;
  critical: string;
  track: string;
  font: string;
  mono: string;
}

function read(): ChartTheme {
  const s = getComputedStyle(document.documentElement);
  const v = (name: string) => s.getPropertyValue(name).trim();
  return {
    surface: v("--chart-surface"),
    grid: v("--chart-grid"),
    axis: v("--chart-axis"),
    label: v("--chart-label"),
    ink: v("--chart-ink"),
    muted: v("--chart-muted"),
    series: [v("--series-1"), v("--series-2"), v("--series-3")],
    good: v("--status-good"),
    warning: v("--status-warning"),
    serious: v("--status-serious"),
    critical: v("--status-critical"),
    track: v("--meter-track"),
    font: "Archivo, ui-sans-serif, system-ui, sans-serif",
    mono: "'IBM Plex Mono', ui-monospace, monospace",
  };
}

export function useChartTheme(): ChartTheme {
  const { resolved } = useTheme();
  const [theme, setTheme] = useState<ChartTheme>(read);
  // read after the theme provider has switched data-theme (it does so in a layout effect)
  useEffect(() => setTheme(read()), [resolved]);
  return theme;
}

const ESC: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** Names in tooltips come from the tools (device and interface names): never trust them as HTML. */
export const escapeHtml = (text: string) => text.replace(/[&<>"']/g, (c) => ESC[c]);
