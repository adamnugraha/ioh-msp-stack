import { KeyRound, LogOut, Monitor, Moon, Plus, Sun, Wrench } from "lucide-react";
import { toast } from "sonner";
import { Link } from "react-router";
import { api, DEMO, type Role } from "@/lib/api";
import { initials } from "@/lib/format";
import { keys, useHealth, useMe } from "@/lib/queries";
import { useTheme, type ThemeChoice } from "@/lib/theme";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useActions } from "@/components/actions/actions";
import { StateDot } from "@/components/status";

export function NewMenu() {
  const actions = useActions();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" className="gap-1.5">
          <Plus /> <span className="hidden md:inline">New</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="text-[12px] font-normal text-muted-foreground">Created in the tool, recorded in the audit trail</DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => actions.ticket({ kind: "Incident" })}>GLPI ticket</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => actions.ticket({ kind: "Change" })}>GLPI change</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => actions.maintenance()}>Zabbix maintenance window</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => actions.addDevice()}>Device in Nautobot</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const METHOD: Record<string, string> = { oidc: "OpenID Connect", saml: "SAML", local: "own login" };

export function ToolsMenu() {
  const { data } = useHealth();
  const open = (url: string, tool: string) => {
    if (DEMO) toast(`Opens ${tool}, signed in with your Keycloak account`, { description: `${url} (the tools are not connected to the preview)` });
    else window.open(url, "_blank", "noopener,noreferrer");
  };
  const down = (data ?? []).filter((t) => !t.ok).length;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1.5" aria-label="Open a tool">
          <Wrench />
          <span className="hidden md:inline">Tools</span>
          {down > 0 && <StateDot tone="bad" />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[320px]">
        <DropdownMenuLabel className="text-[12px] font-normal leading-snug text-muted-foreground">
          One staff login for every tool. Each one opens already signed in through Keycloak.
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {(data ?? []).filter((t) => t.tool !== "Keycloak").map((t) => (
          <DropdownMenuItem key={t.tool} onSelect={() => open(t.url, t.tool)} className="items-start gap-2.5 py-2">
            <StateDot tone={t.ok ? "ok" : "bad"} className="mt-1.5" />
            <span className="min-w-0 flex-1">
              <span className="flex items-baseline justify-between gap-2">
                <span className="font-medium">{t.tool}</span>
                <span className="text-[11.5px] text-muted-foreground">{METHOD[t.signIn.method]}</span>
              </span>
              <span className="block truncate text-[12px] text-muted-foreground">{t.purpose}</span>
            </span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/tools">
            <KeyRound /> Tools, roles and sign-in
          </Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function ThemeMenu() {
  const { choice, resolved, setChoice } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Theme">
          {resolved === "dark" ? <Moon /> : <Sun />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuRadioGroup value={choice} onValueChange={(v) => setChoice(v as ThemeChoice)}>
          <DropdownMenuRadioItem value="dark"><Moon /> Dark</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light"><Sun /> Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system"><Monitor /> As the system</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const PREVIEW_ROLES: { value: string; label: string; roles: Role[] }[] = [
  { value: "all", label: "Manager, engineer and automation", roles: ["manager", "engineer", "automation"] },
  { value: "engineer-automation", label: "Engineer and automation", roles: ["engineer", "automation"] },
  { value: "engineer", label: "Engineer only", roles: ["engineer"] },
  { value: "manager", label: "Manager only", roles: ["manager"] },
];

export function UserMenu() {
  const { data: me } = useMe();
  const client = useQueryClient();
  if (!me) return null;
  const current = PREVIEW_ROLES.find((p) => p.roles.length === me.roles.length && p.roles.every((r) => me.roles.includes(r)))?.value ?? "all";
  const signOut = () => {
    if (DEMO) {
      toast("Signs you out of the console and of Keycloak", { description: "In the preview nothing happens." });
      return;
    }
    const form = document.createElement("form");
    form.method = "post";
    form.action = "/auth/logout";
    const field = document.createElement("input");
    field.type = "hidden";
    field.name = "csrf";
    field.value = me.csrf;
    form.appendChild(field);
    document.body.appendChild(form);
    form.submit();
  };
  const account = () => {
    if (DEMO) toast("Opens your Keycloak account page", { description: `${me.accountUrl}: password, two-factor sign-in, sessions` });
    else window.open(me.accountUrl, "_blank", "noopener,noreferrer");
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="rounded-full" aria-label={`Signed in as ${me.name}`}>
          <span className="flex size-8 items-center justify-center rounded-full bg-primary text-[12px] font-semibold text-primary-foreground">{initials(me.name)}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel className="font-normal">
          <div className="font-semibold">{me.name}</div>
          <div className="text-[12.5px] text-muted-foreground">{me.email}</div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {me.roles.map((r) => (
              <span key={r} className="rounded bg-secondary px-1.5 py-px text-[11.5px] text-secondary-foreground">{r}</span>
            ))}
          </div>
        </DropdownMenuLabel>
        {DEMO && api.setPreviewRoles && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="text-[12px] font-normal text-muted-foreground">Preview as</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              value={current}
              onValueChange={async (v) => {
                const roles = PREVIEW_ROLES.find((p) => p.value === v)?.roles ?? [];
                const next = await api.setPreviewRoles!(roles);
                client.setQueryData(keys.me, next);
                void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "me" });
                toast(`Now looking as: ${PREVIEW_ROLES.find((p) => p.value === v)?.label}`);
              }}
            >
              {PREVIEW_ROLES.map((p) => (
                <DropdownMenuRadioItem key={p.value} value={p.value} className="text-[13px]">
                  {p.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={account}>
          <KeyRound /> Your Keycloak account
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={signOut}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
