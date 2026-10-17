/* The customer's network from Nautobot's cables and circuits, coloured by Zabbix state.
   Shape says what a device is, the outline and the label say its state, so colour is never alone.
   The table view lists every connection. */
import cytoscape from "cytoscape";
import { Maximize2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import type { TopoLink, TopoNode, Topology } from "@/lib/api";
import { speed } from "@/lib/format";
import { useMedia } from "@/hooks/use-media";
import { cn } from "@/lib/utils";
import { useChartTheme, type ChartTheme } from "@/components/charts/chart-theme";
import { Button } from "@/components/ui/button";
import { HostPill, Pill } from "@/components/status";

const STATE_WORD: Record<string, string> = { down: "down", unknown: "unknown", planned: "planned", unmonitored: "not monitored" };

function rank(node: TopoNode, links: TopoLink[]): number {
  if (node.role === "router") return 0;
  if (node.role === "firewall") return 1;
  if (node.role === "switch") {
    const uplink = links.some((l) => {
      const other = l.a === node.id ? l.b : l.b === node.id ? l.a : null;
      return other !== null && /-(rtr|fw)\d+$/.test(other);
    });
    return uplink ? 2 : 3;
  }
  return 3;
}

function positions(topo: Topology): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>();
  const COL = 128;
  const ROW = 96;
  const GAP = 56;
  let x = 0;
  for (const site of topo.sites) {
    const nodes = topo.nodes.filter((n) => n.site === site.id);
    if (!nodes.length) continue;
    const ranks = [...new Set(nodes.map((n) => rank(n, topo.links)))].sort((a, b) => a - b);
    const rows = ranks.map((r) => nodes.filter((n) => rank(n, topo.links) === r).sort((a, b) => a.name.localeCompare(b.name)));
    const width = Math.max(...rows.map((r) => r.length)) * COL;
    rows.forEach((row, ri) => {
      const offset = (width - row.length * COL) / 2;
      row.forEach((n, i) => out.set(n.id, { x: x + offset + i * COL + COL / 2, y: 140 + ri * ROW }));
    });
    x += width + GAP;
  }
  for (const n of topo.nodes.filter((n) => n.role === "network")) out.set(n.id, { x: (x - GAP) / 2, y: 0 });
  return out;
}

function stylesheet(t: ChartTheme): cytoscape.StylesheetJson {
  return [
    {
      selector: "node",
      style: {
        "background-color": t.surface,
        "border-width": 2.5,
        "border-color": t.muted,
        label: "data(label)",
        "font-family": t.font,
        "font-size": 12,
        color: t.ink,
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 6,
        "text-wrap": "wrap",
        "text-max-width": "120px",
        "text-background-color": t.surface,
        "text-background-opacity": 0.85,
        "text-background-padding": "1px",
        width: 32,
        height: 32,
      },
    },
    { selector: "node[role = 'router']", style: { shape: "ellipse" } },
    { selector: "node[role = 'firewall']", style: { shape: "round-hexagon", width: 32 } },
    { selector: "node[role = 'switch']", style: { shape: "round-rectangle", width: 36, height: 24 } },
    { selector: "node[role = 'server']", style: { shape: "barrel", width: 26, height: 32 } },
    { selector: "node[role = 'network']", style: { shape: "round-octagon", width: 52, height: 36, "border-color": t.series[0], "font-weight": 600 } },
    { selector: "node[state = 'up']", style: { "border-color": t.good } },
    { selector: "node[state = 'down']", style: { "border-color": t.critical, "border-width": 4, "font-weight": 600 } },
    { selector: "node[state = 'unknown']", style: { "border-color": t.warning } },
    { selector: "node[state = 'planned']", style: { "border-color": t.axis, "border-style": "dashed", color: t.label } },
    { selector: "node[state = 'unmonitored']", style: { "border-color": t.axis, color: t.label } },
    {
      selector: ":parent",
      style: {
        shape: "round-rectangle",
        "background-color": t.grid,
        "background-opacity": 0.45,
        "border-width": 1,
        "border-color": t.axis,
        label: "data(label)",
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 6,
        "font-size": 12,
        color: t.label,
        "text-background-opacity": 0,
        padding: "22px",
      },
    },
    { selector: "edge", style: { width: 2, "line-color": t.axis, "curve-style": "straight" } },
    { selector: "edge[kind = 'circuit']", style: { "line-color": t.series[0], "line-opacity": 0.55 } },
    { selector: "edge[state = 'down']", style: { "line-color": t.critical, "line-opacity": 1, "line-style": "dashed", width: 2.5 } },
    { selector: "edge[state = 'unknown']", style: { "line-color": t.muted, "line-style": "dotted" } },
    { selector: ".hover", style: { "underlay-color": t.series[0], "underlay-opacity": 0.18, "underlay-padding": 5 } },
    { selector: "node:selected", style: { "overlay-color": t.series[0], "overlay-opacity": 0.12, "overlay-padding": 7 } },
  ];
}

function elements(topo: Topology): cytoscape.ElementDefinition[] {
  const pos = positions(topo);
  const out: cytoscape.ElementDefinition[] = [];
  for (const s of topo.sites) {
    if (topo.nodes.some((n) => n.site === s.id)) out.push({ data: { id: `site:${s.id}`, label: s.name }, selectable: false });
  }
  for (const n of topo.nodes) {
    const word = n.maintenance ? (n.state === "down" ? "down, in maintenance" : "in maintenance") : STATE_WORD[n.state];
    out.push({
      data: { id: n.id, label: word ? `${n.name}\n${word}` : n.name, role: n.role, state: n.state, ...(n.site ? { parent: `site:${n.site}` } : {}) },
      position: pos.get(n.id) ?? { x: 0, y: 0 },
    });
  }
  for (const l of topo.links) out.push({ data: { id: l.id, source: l.a, target: l.b, state: l.state, kind: l.kind } });
  return out;
}

function LinkText({ link, names }: { link: TopoLink; names: Map<string, string> }) {
  return (
    <span>
      <b className="font-medium">{names.get(link.a) ?? link.a}</b> {link.aInterface}
      {" to "}
      <b className="font-medium">{names.get(link.b) ?? link.b}</b> {link.kind === "circuit" ? `(circuit ${link.circuit})` : link.bInterface}
      {", "}
      {speed(link.speed)}, {link.state}
    </span>
  );
}

function rowsOf(topo: Topology): number {
  let rows = 1;
  for (const site of topo.sites) {
    const nodes = topo.nodes.filter((n) => n.site === site.id);
    rows = Math.max(rows, new Set(nodes.map((n) => rank(n, topo.links))).size);
  }
  return rows;
}

export function TopologyView({ topo, customer, height }: { topo: Topology; customer: string; height?: number }) {
  const narrow = useMedia("(max-width: 640px)");
  const boxHeight = height ?? (narrow ? 420 : Math.min(640, Math.max(360, 190 + rowsOf(topo) * 104)));
  const theme = useChartTheme();
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<cytoscape.Core | null>(null);
  const [selected, setSelected] = useState<TopoNode | null>(null);
  const [hover, setHover] = useState<TopoLink | null>(null);
  const [table, setTable] = useState(false);
  const names = useMemo(() => new Map(topo.nodes.map((n) => [n.id, n.name])), [topo]);
  const themeRef = useRef(theme);
  themeRef.current = theme;

  useEffect(() => {
    if (table || !box.current) return;
    const instance = cytoscape({
      container: box.current,
      elements: elements(topo),
      style: stylesheet(themeRef.current),
      layout: { name: "preset", fit: true, padding: 28 },
      minZoom: 0.25,
      maxZoom: 2.5,
      boxSelectionEnabled: false,
      autoungrabify: true,
      selectionType: "single",
    });
    cy.current = instance;
    instance.on("tap", "node", (e) => {
      const id = e.target.id() as string;
      setSelected(topo.nodes.find((n) => n.id === id) ?? null);
    });
    instance.on("tap", (e) => {
      if (e.target === instance) setSelected(null);
    });
    instance.on("mouseover", "edge", (e) => {
      e.target.addClass("hover");
      setHover(topo.links.find((l) => l.id === e.target.id()) ?? null);
    });
    instance.on("mouseout", "edge", (e) => e.target.removeClass("hover"));
    instance.on("tap", "edge", (e) => setHover(topo.links.find((l) => l.id === e.target.id()) ?? null));
    const place = () => {
      instance.resize();
      instance.fit(undefined, 28);
      // On a narrow screen the whole map is too small to read: open it at a readable size on what is
      // down (or on the IOH network) and let the person drag and pinch from there.
      if (instance.zoom() < 0.7) {
        const down = instance.nodes("[state = 'down']");
        instance.zoom(0.85);
        instance.center(down.length ? down : instance.nodes("[role = 'network']").length ? instance.nodes("[role = 'network']") : instance.nodes().first());
      }
    };
    const observer = new ResizeObserver(place);
    observer.observe(box.current);
    return () => {
      observer.disconnect();
      instance.destroy();
      cy.current = null;
    };
  }, [topo, table]);

  useEffect(() => {
    cy.current?.style(stylesheet(theme));
  }, [theme]);

  const counts = {
    down: topo.nodes.filter((n) => n.state === "down").length,
    linksDown: topo.links.filter((l) => l.state === "down").length,
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Legend />
        <div className="flex items-center gap-2">
          {!table && (
            <Button variant="outline" size="sm" onClick={() => cy.current?.fit(undefined, 28)}>
              <Maximize2 /> Whole map
            </Button>
          )}
          <Button variant="outline" size="sm" aria-pressed={table} onClick={() => setTable((v) => !v)}>
            {table ? "Diagram" : "Table"}
          </Button>
        </div>
      </div>

      {table ? (
        <div className="scroll-x rounded-md border">
          <table className="w-full min-w-[640px] text-[13px]">
            <thead className="bg-muted text-left text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">From</th>
                <th className="px-3 py-2 font-medium">To</th>
                <th className="px-3 py-2 font-medium">Speed</th>
                <th className="px-3 py-2 font-medium">State</th>
              </tr>
            </thead>
            <tbody>
              {topo.links.map((l) => (
                <tr key={l.id} className="border-t">
                  <td className="px-3 py-1.5">
                    <b className="font-medium">{names.get(l.a) ?? l.a}</b> <span className="font-mono text-[12px]">{l.aInterface}</span>
                  </td>
                  <td className="px-3 py-1.5">
                    <b className="font-medium">{names.get(l.b) ?? l.b}</b>{" "}
                    <span className="font-mono text-[12px]">{l.kind === "circuit" ? l.circuit : l.bInterface}</span>
                  </td>
                  <td className="px-3 py-1.5">{speed(l.speed)}</td>
                  <td className="px-3 py-1.5">
                    <Pill tone={l.state === "up" ? "ok" : l.state === "down" ? "bad" : "warn"}>{l.state}</Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative overflow-hidden rounded-md border bg-card">
          <div ref={box} style={{ height: boxHeight }} className="w-full" role="img" aria-label={`Network diagram: ${topo.nodes.length} devices, ${counts.down} down, ${counts.linksDown} connections down. The table view lists every connection.`} />
          {selected && (
            <div className="absolute bottom-3 left-3 right-3 rounded-md border bg-popover p-3 text-[13px] shadow-sm sm:right-auto sm:w-80">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-semibold">{selected.name}</div>
                  <div className="text-muted-foreground">
                    {selected.role === "network" ? "Circuits to the IOH network" : `${selected.role}${selected.ip ? `, ${selected.ip}` : ""}`}
                  </div>
                </div>
                {selected.state === "unmonitored" ? (
                  <Pill tone="neutral" icon={null}>not monitored</Pill>
                ) : (
                  selected.state !== "external" && <HostPill state={selected.state} maintenance={selected.maintenance} />
                )}
              </div>
              {selected.role !== "network" && (
                <Link to={`/customers/${customer}/devices/${selected.name}`} className="mt-2 inline-block text-primary hover:underline">
                  Open the device page
                </Link>
              )}
            </div>
          )}
        </div>
      )}
      <p className={cn("min-h-[20px] text-[12.5px] text-muted-foreground", table && "hidden")}>
        {hover ? (
          <LinkText link={hover} names={names} />
        ) : narrow ? (
          "Drag to move, pinch to zoom. Tap a connection for its ports, a device for its details."
        ) : (
          "Point at a connection to see its ports and speed; select a device for its details."
        )}
      </p>
    </div>
  );
}

function Legend() {
  const shape = (cls: string, label: string) => (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className={cn("inline-block border-2 border-[var(--chart-label)]", cls)} />
      {label}
    </span>
  );
  const state = (color: string, label: string, dashed = false) => (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className={cn("inline-block size-3 rounded-full border-[2.5px]", dashed && "border-dashed")} style={{ borderColor: color }} />
      {label}
    </span>
  );
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12.5px] text-muted-foreground">
      {shape("size-3 rounded-full", "router")}
      {shape("h-2.5 w-3.5 rounded-[3px]", "switch")}
      {shape("size-3 rounded-[2px] rotate-45 scale-75", "firewall")}
      {shape("h-3.5 w-2.5 rounded-[4px]", "server")}
      <span className="hidden h-3 w-px bg-border sm:inline-block" />
      {state("var(--status-good)", "up")}
      {state("var(--status-critical)", "down")}
      {state("var(--status-warning)", "unknown")}
      {state("var(--chart-axis)", "planned", true)}
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden className="inline-block w-5 border-t-2 border-dashed" style={{ borderColor: "var(--status-critical)" }} />
        connection down
      </span>
    </div>
  );
}
