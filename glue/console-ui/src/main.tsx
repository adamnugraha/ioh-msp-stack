import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, createMemoryRouter, RouterProvider } from "react-router";
import "./index.css";
import { DEMO } from "@/lib/api";
import { ThemeProvider } from "@/lib/theme";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { routes } from "./routes";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 20_000, retry: 1, refetchOnWindowFocus: !DEMO },
    mutations: { retry: false },
  },
});

// The kit's console uses real addresses (/customers/acme), so links from chat alerts open the right page.
// The preview keeps its place in memory: the page that hosts it gives it no address of its own.
const router = DEMO ? createMemoryRouter(routes, { initialEntries: ["/"] }) : createBrowserRouter(routes);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={300}>
          <RouterProvider router={router} />
          <Toaster position="bottom-right" richColors={false} closeButton duration={6000} />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
);
