/* The tools behind the console, whether each one answers, and how staff sign in to it. */
import { ExternalLink, KeyRound } from "lucide-react";
import { DEMO } from "@/lib/api";
import { useHealth, useMe } from "@/lib/queries";
import { Button } from "@/components/ui/button";
import { ErrorBlock, LoadingBlock, Page, PageHeader, Section, TableWrap } from "@/components/page";
import { Pill } from "@/components/status";
import { openTool } from "./customer/layout";

const ROLES: { role: string; console: string; tools: string }[] = [
  {
    role: "msp-manager",
    console: "Reads every page; service levels and exports.",
    tools: "Views Nautobot; User role, read-only, in Zabbix; guest in Semaphore; reads Forgejo; Viewer in Grafana.",
  },
  {
    role: "msp-engineer",
    console: "Acknowledges and investigates problems, opens tickets and changes, plans maintenance, adds devices, re-syncs customers, runs configuration checks.",
    tools: "Changes the inventory and runs jobs in Nautobot; Admin role in Zabbix's NOC engineers group; guest in Semaphore; reads the backups in Forgejo; Editor in Grafana.",
  },
  {
    role: "msp-automation",
    console: "Starts playbooks, for an open ticket or an approved change of the same customer.",
    tools: "Task runner in each customer's Semaphore project; writes playbooks and templates in Forgejo.",
  },
];

export function ToolsPage() {
  const { data, error, isLoading } = useHealth();
  const { data: me } = useMe();
  if (isLoading) return <LoadingBlock />;
  if (error || !data) return <ErrorBlock error={error} />;
  const keycloak = data.find((t) => t.tool === "Keycloak");
  return (
    <Page>
      <PageHeader
        title="Tools and sign-in"
        meta={
          <span>
            One staff account in Keycloak signs you in to the console and, once single sign-on into the tools is on, to Nautobot, Zabbix, Semaphore, Forgejo and Grafana.
            GLPI keeps its own log-in. Signing out of the console signs you out of Keycloak too.
          </span>
        }
        actions={
          me && (
            <Button variant="outline" size="sm" onClick={() => openTool(me.accountUrl, "Keycloak")}>
              <KeyRound /> Your account: password and two-factor
            </Button>
          )
        }
      />
      <div className="space-y-5">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.filter((t) => t.tool !== "Keycloak").map((t) => (
            <section key={t.tool} className="flex flex-col rounded-lg border bg-card p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h2 className="text-[15px] font-semibold">{t.tool}</h2>
                  <p className="text-[13px] text-muted-foreground">{t.purpose}</p>
                </div>
                {t.ok ? <Pill tone="ok">answers</Pill> : <Pill tone="bad">no answer</Pill>}
              </div>
              <dl className="mt-3 grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
                <dt className="text-muted-foreground">Version</dt>
                <dd>{t.version ?? "unknown"}</dd>
                <dt className="text-muted-foreground">Response</dt>
                <dd className="num">{t.ok ? `${t.ms} ms` : t.detail}</dd>
                <dt className="text-muted-foreground">Sign-in</dt>
                <dd>{t.signIn.label}</dd>
                <dt className="text-muted-foreground">Roles</dt>
                <dd className="text-[12.5px] leading-snug">{t.signIn.mapping}</dd>
              </dl>
              <div className="mt-auto pt-4">
                <Button variant="outline" size="sm" onClick={() => openTool(t.url, t.tool)}>
                  <ExternalLink /> Open {t.tool}
                </Button>
              </div>
            </section>
          ))}
        </div>

        <Section title="Roles" description="Given in Keycloak, with mspctl staff add and mspctl staff roles. The console reads them at every sign-in; each tool maps them to its own groups.">
          <TableWrap>
            <table className="w-full min-w-[720px] text-[13px]">
              <thead className="border-b bg-muted/40 text-left">
                <tr>
                  <th className="px-3 py-2 text-[12.5px] font-medium text-muted-foreground">Role</th>
                  <th className="px-3 py-2 text-[12.5px] font-medium text-muted-foreground">In the console</th>
                  <th className="px-3 py-2 text-[12.5px] font-medium text-muted-foreground">In the tools</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {ROLES.map((r) => (
                  <tr key={r.role} className="align-top">
                    <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[12.5px]">{r.role}</td>
                    <td className="px-3 py-2.5">{r.console}</td>
                    <td className="px-3 py-2.5">{r.tools}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Section>

        {keycloak && (
          <Section title="Keycloak" description={keycloak.purpose}>
            <div className="grid gap-3 p-4 text-[13px] md:grid-cols-3">
              <div>
                <div className="text-muted-foreground">State</div>
                <div className="font-medium">{keycloak.ok ? `answers in ${keycloak.ms} ms` : keycloak.detail}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Version</div>
                <div className="font-medium">{keycloak.version ?? "not reported"}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Staff accounts</div>
                <div className="font-medium">Realm msp, roles {keycloak.signIn.mapping.replace("Roles: ", "")}</div>
              </div>
            </div>
            {DEMO && (
              <p className="border-t px-4 py-3 text-[12.5px] text-muted-foreground">
                In the preview the tools are not connected: the buttons say where they would go. The sign-in methods are the ones kit 1.2.0 sets up with
                ./scripts/install.sh sso-tools; GLPI keeps its own log-in.
              </p>
            )}
          </Section>
        )}
      </div>
    </Page>
  );
}

export function NotFoundPage() {
  return (
    <Page>
      <PageHeader title="Not found" meta={<span>There is no page at this address. Use the menu or search (Ctrl K).</span>} />
    </Page>
  );
}
