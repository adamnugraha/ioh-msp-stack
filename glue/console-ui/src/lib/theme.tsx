/* The console is dark with white text unless someone picks light, or "as the system", in the theme
   menu. The choice is kept in the browser where that is allowed; without storage it lasts until the
   page is closed. "As the system" follows the page hosting the console when it says light or dark,
   otherwise the operating system. */
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type ThemeChoice = "dark" | "light" | "system";
type Resolved = "light" | "dark";

interface ThemeValue {
  choice: ThemeChoice;
  resolved: Resolved;
  setChoice: (choice: ThemeChoice) => void;
}

const KEY = "msp-console-theme";
const DEFAULT: ThemeChoice = "dark";
const ThemeContext = createContext<ThemeValue>({ choice: DEFAULT, resolved: "dark", setChoice: () => {} });

function stored(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const root = document.documentElement;
  // what the hosting page asks for (the preview runs inside another page that may set data-theme)
  const [hostTheme, setHostTheme] = useState<string | null>(() => root.getAttribute("data-theme"));
  const [choice, setChoiceState] = useState<ThemeChoice>(stored);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);
  const applied = useRef<string | null>(null);

  const resolved: Resolved =
    choice !== "system" ? choice : hostTheme === "dark" || hostTheme === "light" ? hostTheme : systemDark ? "dark" : "light";

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemDark(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // set before paint, so charts that read the colours afterwards get the right ones
  useLayoutEffect(() => {
    applied.current = resolved;
    root.setAttribute("data-theme", resolved);
  }, [resolved, root]);

  // The hosting page may change data-theme on its own: remember what it wants (for "as the system"),
  // and keep the console's own choice otherwise.
  useEffect(() => {
    const observer = new MutationObserver(() => {
      const now = root.getAttribute("data-theme");
      if (now === applied.current) return;
      setHostTheme(now);
      if (choice !== "system" && applied.current) root.setAttribute("data-theme", applied.current);
    });
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, [choice, root]);

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      // storage blocked: the choice lasts for this visit
    }
  }, []);

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved, setChoice]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}
