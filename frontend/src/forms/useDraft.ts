import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBeforeUnload, useBlocker } from "react-router";

import { ApiError } from "../api/client";

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

  const blocker = useBlocker(({ currentLocation, nextLocation }) => unsaved.current && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state === "blocked") {
      if (window.confirm("You have changes that are not saved. Leave and lose them?")) blocker.proceed();
      else blocker.reset();
    }
  }, [blocker]);
  useBeforeUnload(useCallback((event) => {
    if (unsaved.current) event.preventDefault();
  }, []));

  return { value, changes, set, dirty, reset, errors, failed };
}
