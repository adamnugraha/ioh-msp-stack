# MSP Console front end

The pages of the MSP Console from kit 1.2.0 on: React, TypeScript, Tailwind CSS and shadcn/ui, with
ECharts for the Zabbix charts and Cytoscape.js for the network map. The console's FastAPI service
serves the built files and answers the pages through its JSON application programming interface
(API) under `/api/v1`. Sign-in, roles, the audit trail and every check on an action stay in the
Python service, as in 1.1.

## Two builds from one code base

| Command | Result |
|---|---|
| `npm run build` | The console the kit ships, written to `../msp_glue/console/ui`. Data comes from `/api/v1`. |
| `npm run build:demo` | One self-contained page in `dist-demo/index.html` with sample data and no network calls. This is the clickable preview. |

The only differences between them are in `src/lib/api/index.ts` (which data source the pages use)
and `src/main.tsx` (the preview keeps its page addresses in memory, because the page hosting it gives
it no address of its own; the kit uses real addresses, so links in chat alerts open the right page).

## Where things are

| Path | What it holds |
|---|---|
| `src/lib/api/types.ts` | The contract with the back end: every type a page reads and every request an action sends. The FastAPI side implements exactly this. |
| `src/lib/api/http.ts` | The client for the real API: session cookie, cross-site request forgery token on every change, plain-words errors. |
| `src/lib/api/demo/` | The sample data and a stand-in API that follows the console's rules (roles, approved changes for changing templates, device limits). |
| `src/lib/queries.ts` | TanStack Query hooks: caching, refresh intervals, toasts after actions. |
| `src/components/actions/` | The action dialogs and the Investigate panel. |
| `src/components/charts/` | Chart pieces. Colours come from the tokens in `src/index.css`, checked with a colour-vision palette validator for light and dark. |
| `src/pages/` | One file per page; the customer page has one component per tab. |
| `src/components/ui/` | shadcn/ui components, added with the shadcn command-line tool and kept as generated except where a comment says why. |

## Working on it

```bash
npm ci
npm run dev:demo        # the preview with sample data, at http://localhost:5173
npm run dev             # against a lab console on port 8088 (see vite.config.ts)
npm run typecheck
```

Package versions are pinned exactly, and only versions at least two weeks old were taken.

## Rules the pages follow

- Every number says where it comes from and how fresh it is; a tool that does not answer is named on
  the page instead of breaking it.
- Status is never colour alone: pills carry an icon and a word, severity has its name next to the
  Zabbix colour, the network map uses shape and label as well as colour.
- Every chart has a table view with the same numbers. One measure per chart; no second y-axis.
- Every action says which tool it changes and what is written to the ticket, and is recorded in the
  audit trail by the back end before it starts.
- Pages work from 360 pixels wide: wide tables scroll inside their card, the page itself never
  scrolls sideways.
