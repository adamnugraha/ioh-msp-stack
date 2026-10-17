import { ChevronRight, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { Freshness } from "@/lib/api";
import { ago } from "@/lib/format";
import { cn } from "@/lib/utils";
import { toneText } from "@/components/status";
import type { Tone } from "@/lib/status";

export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("gutter mx-auto w-full max-w-[1680px] pb-16 pt-5 md:pt-6", className)}>{children}</div>;
}

export interface Crumb {
  label: string;
  to?: string;
}

export function PageHeader({
  crumbs,
  title,
  badge,
  meta,
  actions,
  children,
}: {
  crumbs?: Crumb[];
  title: ReactNode;
  badge?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
      <div className="min-w-0">
        {crumbs && crumbs.length > 0 && (
          <nav aria-label="Breadcrumb" className="mb-1 flex flex-wrap items-center gap-1 text-[13px] text-muted-foreground">
            {crumbs.map((c, i) => (
              <span key={i} className="inline-flex items-center gap-1">
                {c.to ? (
                  <Link to={c.to} className="hover:text-foreground hover:underline">
                    {c.label}
                  </Link>
                ) : (
                  <span>{c.label}</span>
                )}
                <ChevronRight aria-hidden className="size-3.5 opacity-60" />
              </span>
            ))}
          </nav>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-[22px] font-semibold leading-tight md:text-[24px]">{title}</h1>
          {badge}
        </div>
        {meta && <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px] text-muted-foreground">{meta}</div>}
        {children}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Section({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <section id={id} className={cn("min-w-0 rounded-lg border bg-card text-card-foreground", className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-[15px] font-semibold leading-snug">{title}</h2>}
            {description && <p className="mt-0.5 text-[13px] text-muted-foreground">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={cn("min-w-0", bodyClassName)}>{children}</div>
    </section>
  );
}

/** A stat tile: label, value, and one line that explains it. */
export function Stat({
  label,
  value,
  note,
  tone,
  to,
  children,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: Tone;
  to?: string;
  children?: ReactNode;
}) {
  const body = (
    <>
      <div className="text-[13px] text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-[26px] font-semibold leading-none tracking-tight", tone && tone !== "ok" && toneText(tone))}>{value}</div>
      {note && <div className="mt-2 text-[12.5px] leading-snug text-muted-foreground">{note}</div>}
      {children}
    </>
  );
  const cls = cn(
    "relative block min-w-0 rounded-lg border bg-card px-4 py-3.5 text-card-foreground",
    tone === "bad" && "border-l-[3px] border-l-[var(--status-critical)]",
    tone === "warn" && "border-l-[3px] border-l-[var(--status-warning)]",
  );
  return to ? (
    <Link to={to} className={cn(cls, "transition-colors hover:bg-accent/40 focus-visible:ring-[3px] focus-visible:ring-ring/50")}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

export function Empty({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("px-4 py-8 text-center text-[13px] text-muted-foreground", className)}>{children}</div>;
}

/** Where the numbers come from and how fresh they are; tools that did not answer are named. */
export function FreshnessNote({ freshness, sources }: { freshness?: Freshness; sources: string }) {
  if (!freshness) return null;
  return (
    <div className="flex flex-col gap-2">
      <span className="text-[12.5px] text-muted-foreground">
        From {sources}. Read {ago(freshness.loaded)}.
      </span>
      {freshness.errors.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-[13px] text-warn">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <div>
            {freshness.errors.map((e) => (
              <div key={e.source}>
                {e.source} did not answer{e.since ? `; showing what it said ${e.since}` : ""}: {e.error}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex min-h-[200px] items-center justify-center text-[13px] text-muted-foreground" role="status">
      {label}…
    </div>
  );
}

export function ErrorBlock({ error }: { error: unknown }) {
  return (
    <div className="m-4 flex items-start gap-2 rounded-md border border-bad/30 bg-bad-bg px-3 py-2.5 text-[13px] text-bad">
      <TriangleAlert className="mt-0.5 size-4 shrink-0" />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  );
}

/** A table that scrolls sideways inside its card on narrow screens instead of widening the page. */
export function TableWrap({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("scroll-x", className)}>{children}</div>;
}
