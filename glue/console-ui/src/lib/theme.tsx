/* Light, dark, or as the system (or the page hosting the console) says. The choice is kept in the
   browser where that is allowed; without storage it lasts until the page is closed. */
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";

export type ThemeChoice = "system" | "light" | "dark";
type Resolved = "light" | "dark";

interface ThemeValue {
  choice: ThemeChoice;
  resolved: Resolved;
  setChoice: (choice: ThemeChoice) => void;
}

const KEY = "msp-console-theme";
const ThemeContext = createContext<ThemeValue>({ choice: "system", resolved: "light", setChoice: () => {} });

function stored(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const root = document.documentElement;
  // what the hosting page set before the console touched it, so "system" can give it back
  const [hostTheme, setHostTheme] = useState<string | null>(() => root.getAttribute("data-theme"));
  const [choice, setChoiceState] = useState<ThemeChoice>(stored);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemDark(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useLayoutEffect(() => {
    if (choice === "system") {
      if (hostTheme) root.setAttribute("data-theme", hostTheme);
      else root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", choice);
    }
  }, [choice, hostTheme, root]);

  // the hosting page may switch its theme later; follow it while the choice is "system"
  useEffect(() => {
    const observer = new MutationObserver(() => {
      const now = root.getAttribute("data-theme");
      if (choice === "system" && now !== hostTheme) setHostTheme(now);
    });
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, [choice, hostTheme, root]);

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next);
    try {
      if (next === "system") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, next);
    } catch {
      // storage blocked: the choice lasts for this visit
    }
  }, []);

  const resolved: Resolved =
    choice !== "system" ? choice : hostTheme === "dark" ? "dark" : hostTheme === "light" ? "light" : systemDark ? "dark" : "light";

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved, setChoice]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}
