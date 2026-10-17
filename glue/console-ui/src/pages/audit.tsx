/* The audit trail: every change made from the console, who made it, and what came of it. */
import { Download, X } from "lucide-react";
import { useMemo } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { api, DEMO, type AuditFilter } from "@/lib/api";
import { ago, when } from "@/lib/format";
import { useAudit, useCustomers, useSessions } from "@/lib/queries";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AuditTable } from "@/components/audit-table";
import { ErrorBlock, LoadingBlock, Page, PageHeader, Section, TableWrap } from "@/components/page";

const FIELDS: { key: keyof AuditFilter; label: string; all: string }[] = [
  { key: "customer", label: "Customer", all: "All customers" },
  { key: "email", label: "Person", all: "Everyone" },
  { key: "action", label: "Action", all: "All actions" },
  { key: "outcome", label: "Outcome", all: "Any outcome" },
];

export function AuditPage() {
  const [params, setParams] = useSearchParams();
  const filter: AuditFilter = useMemo(
    () => Object.fromEntries(FIELDS.map((f) => [f.key, params.get(f.key) ?? ""]).filter(([, v]) => v)),
    [params],
  );
  const { data, error, isLoading, isFetching } = useAudit(filter);
  const { data: sessions } = useSessions();
  const { data: customers } = useCustomers();
  const names = useMemo(() => Object.fromEntries((customers ?? []).map((c) => [c.code, c.name])), [customers]);
  const set = (key: keyof AuditFilter, value: string) => {
    const next = new URLSearchParams(params);
    if (value === "all") next.delete(key);
    else next.set(key, value);
    setParams(next);
  };
  const exportCsv = () => {
    const url = api.exportUrl("audit", filter as Record<string, string>);
    if (!url || DEMO) toast("Downloads the audit trail as CSV", { description: "With the filters you set. Not available in the preview." });
    else window.location.assign(url);
  };
  if (isLoading) return <LoadingBlock />;
  if (error || !data) return <ErrorBlock error={error} />;
  const filtered = Object.keys(filter).length > 0;
  return (
    <Page>
      <PageHeader
        title="Audit trail"
        meta={<span>Every action from the console is written here before it starts, and its outcome when it ends.</span>}
        actions={
          <Button variant="outline" size="sm" onClick={exportCsv}>
            <Download /> Export CSV
          </Button>
        }
      />
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          {FIELDS.map((f) => (
            <Select key={f.key} value={filter[f.key] || "all"} onValueChange={(v) => set(f.key, v)}>
              <SelectTrigger size="sm" className="w-44" aria-label={f.label}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{f.all}</SelectItem>
                {data.choices[f.key as keyof typeof data.choices].map((v) => (
                  <SelectItem key={v} value={v}>
                    {f.key === "customer" ? names[v] ?? v : v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ))}
          {filtered && (
            <Button variant="ghost" size="sm" onClick={() => setParams(new URLSearchParams())}>
              <X /> Clear
            </Button>
          )}
        </div>
        <Section title={`${data.entries.length} ${data.entries.length === 1 ? "entry" : "entries"}`} className={isFetching ? "opacity-80" : undefined}>
          <AuditTable entries={data.entries} customerNames={names} />
        </Section>
        <Section title="Signed in now" description="Sessions of the console; each ends after 8 hours or when Keycloak ends it">
          <TableWrap>
            <table className="w-full min-w-[620px] text-[13px]">
              <thead className="border-b bg-muted/40 text-left">
                <tr>
                  <th className="px-3 py-2 text-[12.5px] font-medium text-muted-foreground">Person</th>
                  <th className="px-3 py-2 text-[12.5px] font-medium text-muted-foreground">Roles</th>
                  <th className="px-3 py-2 text-[12.5px] font-medium text-muted-foreground">Since</th>
                  <th className="px-3 py-2 text-[12.5px] font-medium text-muted-foreground">Last seen</th>
                  <th className="px-3 py-2 text-[12.5px] font-medium text-muted-foreground">From</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {(sessions ?? []).map((s) => (
                  <tr key={s.email}>
                    <td className="px-3 py-2">
                      <div className="font-medium">{s.name}</div>
                      <div className="text-[12px] text-muted-foreground">{s.email}</div>
                    </td>
                    <td className="px-3 py-2">{s.roles.join(", ")}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{when(s.since)}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{ago(s.lastSeen)}</td>
                    <td className="px-3 py-2">{s.address}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Section>
      </div>
    </Page>
  );
}
