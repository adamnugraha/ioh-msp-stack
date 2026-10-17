/* The frame around every page: the left rail with the pages and the tools' health, the top bar with
   search, new items, the tools menu, theme and account, and the preview notice in the demo build. */
import { Menu, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { DEMO } from "@/lib/api";
import { useHealth, useMe } from "@/lib/queries";
import { setTimeZone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { StateDot } from "@/components/status";
import { CommandMenu } from "./command-menu";
import { NewMenu, ThemeMenu, ToolsMenu, UserMenu } from "./menus";
import { NAV } from "./nav";
import { PreviewBanner } from "./preview";



export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={className}>
      <rect width="32" height="32" rx="7" fill="#1764c7" />
      <rect x="6" y="7" width="20" height="5" rx="1.5" fill="#fff" />
      <rect x="6" y="14" width="9" height="11" rx="1.5" fill="#fff" opacity=".85" />
      <rect x="17" y="14" width="9" height="5" rx="1.5" fill="#fff" opacity=".7" />
      <rect x="17" y="21" width="9" height="4" rx="1.5" fill="#7ee0a8" />
    </svg>
  );
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Pages" className="flex flex-col gap-0.5 px-2">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2.5 rounded-md px-3 py-2 text-[13.5px] font-medium text-sidebar-foreground/85 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              isActive && "bg-sidebar-accent text-sidebar-accent-foreground",
            )
          }
        >
          <item.icon className="size-4 shrink-0 opacity-90" />
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

function ToolHealth({ onNavigate }: { onNavigate?: () => void }) {
  const { data } = useHealth();
  return (
    <div className="px-4 pb-4">
      <NavLink to="/tools" onClick={onNavigate} className="mb-2 block text-[12px] font-medium text-sidebar-muted hover:text-sidebar-accent-foreground">
        Tools
      </NavLink>
      <ul className="space-y-1.5">
        {(data ?? []).map((t) => (
          <li key={t.tool} className="flex items-center gap-2 text-[12.5px]" title={t.ok ? `${t.tool} ${t.version ?? ""} answered in ${t.ms} ms` : `${t.tool}: ${t.detail}`}>
            <StateDot tone={t.ok ? "ok" : "bad"} />
            <span className="flex-1 text-sidebar-foreground/90">{t.tool}</span>
            <span className="num text-sidebar-muted">{t.ok ? `${t.ms} ms` : "down"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Rail({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-5 pb-5 pt-[max(18px,env(safe-area-inset-top))]">
        <Logo className="size-7" />
        <div className="leading-tight">
          <div className="text-[14.5px] font-semibold text-sidebar-accent-foreground">MSP Console</div>
          <div className="text-[11.5px] text-sidebar-muted">IOH managed services</div>
        </div>
      </div>
      <Nav onNavigate={onNavigate} />
      <div className="mt-auto border-t border-sidebar-border pt-4">
        <ToolHealth onNavigate={onNavigate} />
      </div>
    </div>
  );
}

function Topbar({ onMenu, onSearch }: { onMenu: () => void; onSearch: () => void }) {
  const { data: me } = useMe();
  return (
    <div className="sticky top-0 z-30 border-b bg-background/95 pt-[env(safe-area-inset-top)] backdrop-blur supports-[backdrop-filter]:bg-background/85">
      <div className="gutter flex h-14 items-center gap-2">
        <Button variant="ghost" size="icon" className="-ml-2 lg:hidden" onClick={onMenu} aria-label="Open the menu">
          <Menu />
        </Button>
        <div className="flex items-center gap-2 lg:hidden">
          <Logo className="size-6" />
          <span className="text-[14px] font-semibold">MSP Console</span>
        </div>
        <button
          type="button"
          onClick={onSearch}
          className="ml-auto hidden h-9 w-full max-w-md items-center gap-2 rounded-md border bg-card px-3 text-[13px] text-muted-foreground shadow-xs transition-colors hover:bg-accent sm:flex lg:ml-0"
        >
          <Search className="size-4" />
          <span className="flex-1 text-left">Search customers, devices, problems</span>
          <kbd className="rounded border bg-muted px-1.5 py-px text-[11px]">Ctrl K</kbd>
        </button>
        <div className="ml-auto flex items-center gap-1 sm:gap-1.5">
          <Button variant="ghost" size="icon" className="sm:hidden" onClick={onSearch} aria-label="Search">
            <Search />
          </Button>
          {me?.canAct && <NewMenu />}
          <ToolsMenu />
          <ThemeMenu />
          <UserMenu />
        </div>
      </div>
    </div>
  );
}

export function AppShell() {
  const { data: me } = useMe();
  if (me) setTimeZone(me.timezone);
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState(false);
  const location = useLocation();

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [location.pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        setSearch((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex min-h-dvh bg-background">
      <aside className="sticky top-0 hidden h-dvh w-[236px] shrink-0 overflow-y-auto bg-sidebar text-sidebar-foreground lg:block">
        <Rail />
      </aside>
      <Sheet open={menu} onOpenChange={setMenu}>
        <SheetContent side="left" className="w-[264px] border-0 bg-sidebar p-0 text-sidebar-foreground" showCloseButton={false}>
          <SheetHeader className="sr-only">
            <SheetTitle>Menu</SheetTitle>
            <SheetDescription>Pages of the MSP Console and the tools' health</SheetDescription>
          </SheetHeader>
          <Rail onNavigate={() => setMenu(false)} />
        </SheetContent>
      </Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        {DEMO && <PreviewBanner />}
        <Topbar onMenu={() => setMenu(true)} onSearch={() => setSearch(true)} />
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
      <CommandMenu open={search} onOpenChange={setSearch} />
    </div>
  );
}
