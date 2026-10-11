import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";

import { get, list, type Query } from "../api/client";

export interface PickerProps<T extends { id: number }> {
  endpoint: string;
  value: number | null;
  onChange: (id: number | null, row: T | null) => void;
  label: (row: T) => string;
  /** A second line under each choice (a code, a balance). */
  detail?: (row: T) => string;
  /** Narrowing every search carries (?role_assignments__role=customer). */
  fixed?: Query;
  placeholder?: string;
  disabled?: boolean;
  invalid?: boolean;
  id?: string;
  ariaLabel?: string;
  autoFocus?: boolean;
}

/**
 * Choose one record of a list too long to show whole: type part of its
 * name or code, pick from what the server finds. Arrow keys and Enter, or
 * the mouse. Asks the server as typing pauses, eight at a time; the one
 * chosen is shown by name, read once if the page did not already have it.
 */
export function RecordPicker<T extends { id: number }>(props: PickerProps<T>) {
  const { endpoint, value, onChange, label, detail, fixed, placeholder, disabled, invalid, ariaLabel, autoFocus } = props;
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [search, setSearch] = useState("");
  const [active, setActive] = useState(0);
  const [chosen, setChosen] = useState<T | null>(null);
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(typed), 200);
    return () => window.clearTimeout(timer);
  }, [typed]);

  const found = useQuery({
    queryKey: ["pick", endpoint, fixed, search],
    queryFn: ({ signal }) => list<T>(endpoint, { ...fixed, search, page_size: 8 }, signal),
    enabled: open,
    staleTime: 30_000,
  });
  const current = useQuery({
    queryKey: ["record", endpoint, String(value)],
    queryFn: ({ signal }) => get<T>(`${endpoint}${value}/`, undefined, signal),
    enabled: value !== null && chosen?.id !== value,
    staleTime: 60_000,
  });
  const shown = value === null ? null : chosen?.id === value ? chosen : current.data ?? null;
  const rows = found.data?.rows ?? [];

  const choose = (row: T | null) => {
    setChosen(row);
    onChange(row ? row.id : null, row);
    setOpen(false);
    setTyped("");
  };

  return (
    <div className={`picker${invalid ? " invalid" : ""}${disabled ? " disabled" : ""}`}>
      <input
        ref={box}
        id={props.id}
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-invalid={invalid || undefined}
        aria-activedescendant={open && rows[active] ? `${listId}-${active}` : undefined}
        autoFocus={autoFocus}
        disabled={disabled}
        placeholder={shown ? undefined : placeholder ?? "Type to search"}
        value={open ? typed : shown ? label(shown) : ""}
        onFocus={() => {
          setOpen(true);
          setActive(0);
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onChange={(event) => {
          setTyped(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setOpen(true);
            setActive((i) => Math.min(i + 1, rows.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((i) => Math.max(i - 1, 0));
          } else if (event.key === "Enter" && open) {
            event.preventDefault();
            if (rows[active]) choose(rows[active]!);
          } else if (event.key === "Escape") {
            setOpen(false);
            setTyped("");
          } else if (event.key === "Backspace" && !typed && shown && !open) {
            choose(null);
          }
        }}
      />
      {open && (
        <ul id={listId} role="listbox" className="picker-list">
          {found.isPending && <li className="none">Searching…</li>}
          {found.isError && <li className="none">Could not search: {found.error.message}</li>}
          {!found.isPending && rows.length === 0 && <li className="none">Nothing matches “{search}”.</li>}
          {rows.map((row, index) => (
            <li
              key={row.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              className={index === active ? "active" : undefined}
              onMouseEnter={() => setActive(index)}
              onMouseDown={(event) => {
                event.preventDefault();
                choose(row);
              }}
            >
              <span>{label(row)}</span>
              {detail && <small>{detail(row)}</small>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
