import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { csrfToken } from "../api/client";
import { MODULES, offered, screenUrl } from "../app/registry";
import { useAccess } from "../auth/me";
import { CommandPalette, type Command } from "./CommandPalette";
import { Icon } from "./Icon";

function useOnline(): boolean {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}

function singular(label: string): string {
  const special: Record<string, string> = {
    "Money received": "receipt", "Money paid": "payment", Deliveries: "delivery", "Purchase orders": "purchase order", "Journal entries": "journal entry", "Pay runs": "pay run",
  };
  return special[label] ?? label.toLowerCase().replace(/s$/, "");
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "?";
}

/**
 * The frame every screen sits in: the modules down the left, the
 * module's screens across the top, and Ctrl+K to jump anywhere.
 * Only what the person may read is offered.
 */
export function Shell() {
  const { me, can } = useAccess();
  const location = useLocation();
  const online = useOnline();
  const [palette, setPalette] = useState(false);
  const [menu, setMenu] = useState(false);

  const modules = useMemo(
    () =>
      MODULES.map((module) => ({ ...module, screens: module.screens.filter((screen) => offered(screen, can)) }))
        .filter((module) => module.screens.length > 0),
    [can],
  );
  const commands: Command[] = useMemo(
    () => [
      { label: "Home", hint: "Start", href: "/" },
      ...modules.flatMap((module) =>
        module.screens.flatMap((screen) => [
          { label: screen.label, hint: module.label, href: screenUrl(module, screen), keywords: screen.keywords },
          ...(screen.detail && screen.create && can(screen.create)
            ? [{ label: `New ${singular(screen.label)}`, hint: module.label, href: `${screenUrl(module, screen)}/new`, keywords: `create add ${screen.keywords ?? ""}` }]
            : []),
        ]),
      ),
    ],
    [modules, can],
  );
  const current = modules.find((module) => location.pathname.startsWith(`/${module.key}/`));

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => setMenu(false), [location.pathname]);

  return (
    <div className="shell">
      <nav className="rail" aria-label="Modules">
        <NavLink to="/" end className="brand" aria-label="Home">
          <span>DP</span>
        </NavLink>
        <NavLink to="/" end className="rail-item">
          <Icon name="home" />
          <span>Home</span>
        </NavLink>
        {modules.map((module) => (
          <NavLink
            key={module.key}
            to={screenUrl(module, module.screens[0]!)}
            className={() => (current?.key === module.key ? "rail-item active" : "rail-item")}
          >
            <Icon name={module.icon} />
            <span>{module.label}</span>
          </NavLink>
        ))}
        <div className="rail-foot">
          <button type="button" className="avatar" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((open) => !open)} title={me.name}>
            {initials(me.name)}
          </button>
          {menu && (
            <div className="user-menu" role="menu">
              <strong>{me.name}</strong>
              <small>{me.roles.join(", ") || (me.is_superuser ? "Administrator" : "No role yet")}</small>
              <form method="post" action="/accounts/logout/">
                <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken()} />
                <input type="hidden" name="next" value="/accounts/login/?next=/app/" />
                <button type="submit" role="menuitem">Sign out</button>
              </form>
            </div>
          )}
        </div>
      </nav>
      <div className="main">
        <header className="topbar">
          <div className="crumbs">
            <span className="company">{me.company}</span>
            {current && <span className="sep">/</span>}
            {current && <span>{current.label}</span>}
          </div>
          {current && (
            <nav className="tabs" aria-label={`${current.label} screens`}>
              {current.screens.map((screen) => (
                <NavLink key={screen.path} to={screenUrl(current, screen)}>{screen.label}</NavLink>
              ))}
            </nav>
          )}
          <button type="button" className="goto" onClick={() => setPalette(true)}>
            <span>Go to…</span>
            <kbd>Ctrl K</kbd>
          </button>
        </header>
        {!online && (
          <div className="offline" role="status">
            No network. What is on screen may be out of date; nothing can be saved until it is back.
          </div>
        )}
        <main className="content">
          <Outlet />
        </main>
      </div>
      <CommandPalette commands={commands} open={palette} onClose={() => setPalette(false)} />
    </div>
  );
}
