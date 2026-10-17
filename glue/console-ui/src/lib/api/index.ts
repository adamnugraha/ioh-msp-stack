/* One client for the whole app. The demo build answers from sample data in the page; the kit's build
   talks to the console's API. The other one is left out of each bundle. */
import { createDemoApi } from "./demo";
import { createHttpApi } from "./http";
import type { ConsoleApi } from "./types";

export const DEMO = import.meta.env.MODE === "demo";
export const api: ConsoleApi = DEMO ? createDemoApi() : createHttpApi();
export * from "./types";
