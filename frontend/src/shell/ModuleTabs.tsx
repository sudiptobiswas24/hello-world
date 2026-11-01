import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router";

import { GROUPS } from "../app/groups";
import { screenUrl, type Module, type Screen } from "../app/registry";

interface Entry {
  label: string;
  screens: Screen[];
}

/** The module's offered screens in its groups (app/groups.ts); what no group names, under Other. */
export function entriesOf(module: Module): Entry[] {
  const byPath = new Map(module.screens.map((screen) => [screen.path, screen]));
  const placed = new Set<string>();
  const entries: Entry[] = [];
  for (const [label, paths] of GROUPS[module.key] ?? []) {
    const screens = paths.flatMap((path) => {
      const screen = byPath.get(path);
      if (!screen) return [];
      placed.add(path);
      return [screen];
    });
    if (screens.length) entries.push({ label, screens });
  }
  const rest = module.screens.filter((screen) => !placed.has(screen.path));
  if (rest.length) entries.push({ label: "Other", screens: rest });
  return entries;
}

/**
 * A module's screens as menus, one per group, and what does not fit under More.
 *
 * Sales offered 33 screens in one row of tabs and a 1366-pixel screen showed
 * ten: the rest sat past the edge of a bar that scrolled without saying so
 * (O392). A group of one screen is a plain link. Every screen stays a link
 * in this navigation, shown or folded, so what reads the navigation (the
 * browser sweep, a screen reader) still finds them all.
 */
export function ModuleTabs({ module }: { module: Module }) {
  const bar = useRef<HTMLElement>(null);
  const widths = useRef<number[]>([]);
  const entries = entriesOf(module);
  const [fit, setFit] = useState(entries.length);
  const [open, setOpen] = useState<string | null>(null);
  const location = useLocation();
  // The module's identity, not the object: the shell may rebuild it on every render.
  const which = `${module.key}:${module.screens.map((screen) => screen.path).join(",")}`;

  // A new module is measured afresh, every entry laid out first.
  useLayoutEffect(() => {
    widths.current = [];
    setFit(entries.length);
  }, [which, entries.length]);

  useLayoutEffect(() => {
    const nav = bar.current;
    if (!nav) return;
    const measure = () => {
      if (widths.current.length !== entries.length) {
        const laid = [...nav.querySelectorAll<HTMLElement>(":scope > .tab-entry")];
        if (laid.length !== entries.length) return; // not all laid out yet
        widths.current = laid.map((entry) => entry.offsetWidth + 2);
      }
      const room = nav.clientWidth;
      const all = widths.current.reduce((sum, width) => sum + width, 0);
      if (all <= room) {
        setFit(entries.length);
        return;
      }
      const more = 90;
      let used = 0;
      let count = 0;
      while (count < widths.current.length && used + (widths.current[count] ?? 0) <= room - more) {
        used += widths.current[count] ?? 0;
        count += 1;
      }
      setFit(count);
    };
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(nav);
    return () => watch.disconnect();
  }, [which, fit, entries.length]);

  useEffect(() => setOpen(null), [location.pathname]);
  useEffect(() => {
    if (open === null) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !bar.current?.contains(event.target as Node)) {
        setOpen(null);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const isHere = (screen: Screen) => {
    const url = screenUrl(module, screen);
    return location.pathname === url || location.pathname.startsWith(`${url}/`);
  };
  const shown = entries.slice(0, fit);
  const folded = entries.slice(fit);
  const toggle = (key: string) => setOpen((was) => (was === key ? null : key));

  const menu = (key: string, here: boolean, title: string, items: Entry[]) => (
    <div className="tab-entry tabs-more" key={key}>
      <button type="button" className={here ? "active" : undefined} aria-haspopup="menu"
              aria-expanded={open === key} onClick={() => toggle(key)}>
        {title} ▾
      </button>
      {/* No aria-label here or on a group: a form's get_by_label("CIN") matched the "Pricing" menu (O392). */}
      <div className="tabs-menu" role="menu" hidden={open !== key}>
        {items.map((entry) => (
          <div key={entry.label} role="group">
            {items.length > 1 && <div className="tabs-menu-head">{entry.label}</div>}
            {entry.screens.map((screen) => (
              <NavLink key={screen.path} to={screenUrl(module, screen)} role="menuitem">{screen.label}</NavLink>
            ))}
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <nav className="tabs" aria-label={`${module.label} screens`} ref={bar}>
      {shown.map((entry) => {
        const only = entry.screens.length === 1 ? entry.screens[0] : undefined;
        if (only) {
          return <NavLink key={entry.label} className="tab-entry" to={screenUrl(module, only)}>{only.label}</NavLink>;
        }
        return menu(entry.label, entry.screens.some(isHere), entry.label, [entry]);
      })}
      {folded.length > 0
        && menu("(more)", folded.some((entry) => entry.screens.some(isHere)),
          shown.length === 0 ? "Screens" : "More", folded)}
    </nav>
  );
}
