import { Navigate, useRouteError, type RouteObject } from "react-router";
import { useMe } from "@/lib/queries";
import { ActionsProvider } from "@/components/actions/actions";
import { ErrorBlock, LoadingBlock, Page } from "@/components/page";
import { AppShell } from "@/components/shell/shell";
import { AuditPage } from "@/pages/audit";
import { ChangesPage } from "@/pages/changes";
import { CustomerLayout } from "@/pages/customer/layout";
import { CustomerOverview } from "@/pages/customer/overview";
import {
  CustomerActivity,
  CustomerAutomation,
  CustomerChanges,
  CustomerDevices,
  CustomerNetwork,
  CustomerWork,
} from "@/pages/customer/tabs";
import { CustomersPage } from "@/pages/customers";
import { DevicePage } from "@/pages/device";
import { OverviewPage } from "@/pages/overview";
import { ServicePage } from "@/pages/service";
import { NotFoundPage, ToolsPage } from "@/pages/tools";

/** Engineers start on the operations page, managers without the engineer role on service levels (as in 1.1). */
function Home() {
  const { data: me } = useMe();
  if (!me) return <LoadingBlock />;
  return <Navigate to={me.canAct || !me.isManager ? "/ops" : "/service"} replace />;
}

function RouteError() {
  const error = useRouteError();
  return (
    <Page>
      <ErrorBlock error={error instanceof Error ? error : new Error("This page could not be shown. Reload it; if it stays like this, the console's log has the reason.")} />
    </Page>
  );
}

export const routes: RouteObject[] = [
  {
    path: "/",
    element: (
      <ActionsProvider>
        <AppShell />
      </ActionsProvider>
    ),
    children: [
      {
        errorElement: <RouteError />,
        children: [
          { index: true, element: <Home /> },
          { path: "ops", element: <OverviewPage /> },
          { path: "customers", element: <CustomersPage /> },
          {
            path: "customers/:code",
            element: <CustomerLayout />,
            children: [
              { index: true, element: <CustomerOverview /> },
              { path: "devices", element: <CustomerDevices /> },
              { path: "network", element: <CustomerNetwork /> },
              { path: "work", element: <CustomerWork /> },
              { path: "changes", element: <CustomerChanges /> },
              { path: "automation", element: <CustomerAutomation /> },
              { path: "activity", element: <CustomerActivity /> },
            ],
          },
          { path: "customers/:code/devices/:name", element: <DevicePage /> },
          { path: "changes", element: <ChangesPage /> },
          { path: "service", element: <ServicePage /> },
          { path: "audit", element: <AuditPage /> },
          { path: "tools", element: <ToolsPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
