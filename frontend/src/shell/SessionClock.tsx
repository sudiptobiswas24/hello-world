import { useEffect, useState } from "react";
import { useLocation } from "react-router";

import { lastActive, request } from "../api/client";
import { forgetKeptDraft, keptDraft, RESTORE } from "../forms/useDraft";

export interface SessionTimes {
  /** Signed out after this long with nothing done (apps/core/session_limits.py). */
  idle_seconds: number;
  /** And in any case this long after /api/core/me/ was read. */
  ends_in_seconds: number;
}

function warnBefore(idle: number): number {
  return Math.min(120, Math.floor(idle / 3));
}

function inWords(seconds: number): string {
  if (seconds >= 90) return `${Math.round(seconds / 60)} minutes`;
  return `${Math.max(0, Math.round(seconds))} seconds`;
}

/**
 * Says before the session ends (L12): for want of use, with a button that
 * keeps it open, or at its twelve-hour limit, which nothing extends. What
 * is being typed is kept either way and offered back after signing in.
 */
export function SessionClock({ times }: { times?: SessionTimes }) {
  const [now, setNow] = useState(Date.now());
  const [read] = useState(Date.now());
  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, []);
  if (!times) return null;
  const warn = warnBefore(times.idle_seconds);
  const idleLeft = times.idle_seconds - (now - lastActive()) / 1000;
  const endLeft = times.ends_in_seconds - (now - read) / 1000;
  if (endLeft <= warn && endLeft < idleLeft) {
    return (
      <div className="session-warning" role="alert">
        Your session reaches its twelve-hour limit in {inWords(endLeft)}. Sign in again then: what you are typing is kept.
      </div>
    );
  }
  if (idleLeft > warn) return null;
  return (
    <div className="session-warning" role="alert">
      {idleLeft > 0
        ? `You will be signed out in ${inWords(idleLeft)} for want of use.`
        : "You were signed out for want of use. Your next step takes you to sign in; what you typed is kept."}
      {idleLeft > 0 && (
        <button type="button" onClick={() => { void request("/api/core/me/").catch(() => undefined); setNow(Date.now()); }}>
          Stay signed in
        </button>
      )}
    </div>
  );
}

/** What was typed here when the session ended, offered back after signing in again. */
export function DraftOffer() {
  // The router's path is under its basename (/app); what was kept is under the browser's own address.
  const location = useLocation();
  const [kept, setKept] = useState(() => keptDraft(window.location.pathname));
  useEffect(() => { setKept(keptDraft(window.location.pathname)); }, [location.pathname]);
  if (!kept) return null;
  const at = new Date(kept.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="session-warning" role="status">
      You were typing here when your session ended, at {at}.
      <button type="button" onClick={() => { window.dispatchEvent(new Event(RESTORE)); setKept(null); }}>Put it back</button>
      <button type="button" className="quiet" onClick={() => { forgetKeptDraft(window.location.pathname); setKept(null); }}>Discard</button>
    </div>
  );
}
