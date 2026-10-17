import { Link } from "react-router";
import type { AuditEntry } from "@/lib/api";
import { when, whenFull } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Empty, TableWrap } from "@/components/page";
import { OutcomePill } from "@/components/status";

const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted-foreground whitespace-nowrap";
const td = "px-3 py-2 align-top";

export function AuditTable({ entries, showCustomer = true, customerNames }: { entries: AuditEntry[]; showCustomer?: boolean; customerNames?: Record<string, string> }) {
  if (!entries.length) return <Empty>Nothing recorded yet.</Empty>;
  return (
    <TableWrap>
      <table className="w-full min-w-[860px] text-[13px]">
        <thead className="border-b bg-muted/40">
          <tr>
            <th className={th}>When</th>
            <th className={th}>Who</th>
            <th className={th}>Action</th>
            {showCustomer && <th className={th}>Customer</th>}
            <th className={th}>What</th>
            <th className={th}>Ticket</th>
            <th className={th}>Outcome</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {entries.map((e) => (
            <tr key={e.id}>
              <td className={cn(td, "whitespace-nowrap")} title={whenFull(e.at)}>{when(e.at)}</td>
              <td className={td}>
                <div className="whitespace-nowrap">{e.name || e.email}</div>
                <div className="text-[12px] text-muted-foreground">{e.address}</div>
              </td>
              <td className={cn(td, "whitespace-nowrap font-medium")}>{e.action}</td>
              {showCustomer && (
                <td className={td}>
                  {e.customer ? (
                    <Link className="hover:underline" to={`/customers/${e.customer}`}>{customerNames?.[e.customer] ?? e.customer}</Link>
                  ) : (
                    <span className="text-muted-foreground">platform</span>
                  )}
                </td>
              )}
              <td className={td}>
                <div>{e.target || <span className="text-muted-foreground">none</span>}</div>
                {e.reason && <div className="text-[12px] text-muted-foreground">{e.reason}</div>}
              </td>
              <td className={cn(td, "whitespace-nowrap")}>{e.ticket}</td>
              <td className={td}>
                <OutcomePill outcome={e.outcome} />
                {e.result && <div className="mt-1 max-w-[360px] text-[12px] leading-snug text-muted-foreground">{e.result}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
