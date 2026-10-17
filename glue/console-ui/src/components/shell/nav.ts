import { Building2, CalendarClock, Gauge, History, KeyRound, LayoutDashboard, type LucideIcon } from "lucide-react";

export const NAV: { to: string; label: string; icon: LucideIcon; end?: boolean }[] = [
  { to: "/ops", label: "Overview", icon: LayoutDashboard },
  { to: "/customers", label: "Customers", icon: Building2 },
  { to: "/changes", label: "Changes and maintenance", icon: CalendarClock },
  { to: "/service", label: "Service levels", icon: Gauge },
  { to: "/audit", label: "Audit trail", icon: History },
  { to: "/tools", label: "Tools and sign-in", icon: KeyRound },
];
