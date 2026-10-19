import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBeforeUnload, useBlocker } from "react-router";

import { ApiError, SIGNING_IN } from "../api/client";

// Every draft open on the page. The router consults only the last guard
// made, so a page with two (a customer and their terms) guarded one of
// them: each guard asks after all of them instead.
const open = new Set<{ current: boolean }>();

/**
 * What was typed, kept in this browser when the session ended (L12): the
 * page goes to sign in, comes back to the same address, and offers it back
 * (DraftOffer). One entry per draft on the page, in the order they opened.
 * Kept under the login that typed it, so the next person at the computer is
 * never offered it; for a day at most; and cleared at sign-out (review F4).
 */
const KEPT = "erp.kept-draft:";
const KEPT_FOR_MS = 24 * 60 * 60 * 1000;
export const RESTORE = "erp:restore-draft";
interface Typed { unsaved: { current: boolean }; changes: { current: Record<string, unknown> }; apply: (kept: Record<string, unknown>) => void }
const typed: Typed[] = [];
let owner: number | null = null;

/** Who is signed in (the shell says so once it knows): a draft is kept and offered for them only. */
export function setDraftOwner(login: number | null): void {
  owner = login;
}

function keyFor(path: string, login: number | null): string | null {
  return login === null ? null : `${KEPT}${login}:${path}`;
}

export function keptDraft(path: string = window.location.pathname, login: number | null = owner,
  now: number = Date.now()): { at: number; drafts: Record<string, unknown>[] } | null {
  const key = keyFor(path, login);
  if (key === null) return null;
  try {
    const text = window.localStorage.getItem(key);
    if (!text) return null;
    const kept = JSON.parse(text) as { at: number; drafts: Record<string, unknown>[] };
    if (!(now - kept.at < KEPT_FOR_MS)) {
      window.localStorage.removeItem(key);
      return null;
    }
    return kept;
  } catch {
    return null;
  }
}

export function forgetKeptDraft(path: string = window.location.pathname, login: number | null = owner): void {
  const key = keyFor(path, login);
  try {
    if (key !== null) window.localStorage.removeItem(key);
  } catch {
    // Storage refused (a private window): nothing was kept to forget.
  }
}

/** Every kept draft in this browser, whoever typed it: at sign-out. */
export function forgetEveryKeptDraft(): void {
  try {
    for (const key of Object.keys(window.localStorage)) if (key.startsWith(KEPT)) window.localStorage.removeItem(key);
  } catch {
    // Storage refused: nothing was kept.
  }
}

if (typeof window !== "undefined") {
  window.addEventListener(SIGNING_IN, () => {
    const drafts = typed.map((draft) => (draft.unsaved.current ? draft.changes.current : {}));
    if (!drafts.some((changes) => Object.keys(changes).length > 0)) return;
    const key = keyFor(window.location.pathname, owner);
    if (key === null) return; // nobody known to keep it for: the leave guard still asks
    try {
      window.localStorage.setItem(key, JSON.stringify({ at: Date.now(), drafts }));
    } catch {
      return; // not kept: the leave guard still asks
    }
    // Kept, so leaving to sign in loses nothing and need not ask.
    for (const draft of typed) draft.unsaved.current = false;
  });
  window.addEventListener(RESTORE, () => {
    const kept = keptDraft();
    if (!kept) return;
    typed.forEach((draft, index) => {
      const changes = kept.drafts[index];
      if (changes && Object.keys(changes).length > 0) draft.apply(changes);
    });
    forgetKeptDraft();
  });
}

/**
 * The header of a document being edited: what was read, what the person
 * changed, and what the server said about it.
 *
 * Only changed fields are sent, so two people editing different fields of
 * one draft do not overwrite each other's work. Leaving with unsaved
 * changes asks first, both within the application and on closing the tab.
 */
export function useDraft<T extends Record<string, unknown>>(saved: T | undefined) {
  const [changes, setChanges] = useState<Partial<T>>({});
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const dirty = Object.keys(changes).length > 0;
  // What the leave-guard asks at the moment of leaving. State lags a
  // render: saved, reset and sent to the new record in one handler, the
  // guard still saw the old changes and asked whether to lose them. A
  // person who said no stayed on the blank form, one click from saving
  // the same order twice.
  const unsaved = useRef(dirty);
  unsaved.current = dirty;

  const value = useMemo(() => ({ ...(saved ?? {}), ...changes }) as T, [saved, changes]);
  const set = useCallback(<K extends keyof T>(key: K, next: T[K]) => {
    setChanges((current) => {
      const updated = { ...current, [key]: next };
      if (saved && saved[key] === next) delete updated[key];
      return updated;
    });
    setErrors((current) => {
      if (!current[key as string]) return current;
      const rest = { ...current };
      delete rest[key as string];
      return rest;
    });
  }, [saved]);

  const reset = useCallback(() => {
    unsaved.current = false;
    setChanges({});
    setErrors({});
  }, []);
  const failed = useCallback((error: unknown) => {
    if (error instanceof ApiError) setErrors(error.fields);
  }, []);

  useLeaveGuard(unsaved);

  // Kept across a sign-in when the session ends (KEPT above).
  const typedNow = useRef(changes as Record<string, unknown>);
  typedNow.current = changes as Record<string, unknown>;
  useEffect(() => {
    const entry: Typed = { unsaved, changes: typedNow, apply: (kept) => setChanges(kept as Partial<T>) };
    typed.push(entry);
    return () => { typed.splice(typed.indexOf(entry), 1); };
  }, []);

  return { value, changes, set, dirty, reset, errors, failed };
}

/**
 * Leaving with changes not saved asks first, within the application and on
 * closing the tab. `unsaved` is a ref, read at the moment of leaving.
 */
export function useLeaveGuard(unsaved: { current: boolean }) {
  useEffect(() => {
    open.add(unsaved);
    return () => { open.delete(unsaved); };
  }, [unsaved]);
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    [...open].some((draft) => draft.current) && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state === "blocked") {
      if (window.confirm("You have changes that are not saved. Leave and lose them?")) blocker.proceed();
      else blocker.reset();
    }
  }, [blocker]);
  useBeforeUnload(useCallback((event) => {
    if (unsaved.current) event.preventDefault();
  }, [unsaved]));
}
