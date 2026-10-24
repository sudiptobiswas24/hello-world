import { useId, useState, type ReactNode } from "react";

import { plantTimeZone } from "../lib/format";

/**
 * One box on a form: its label, the box, and what the server said is
 * wrong with it, tied together for screen readers.
 */
export function Field({ label, errors, hint, children, wide }: {
  label: string;
  errors?: string[];
  hint?: string;
  wide?: boolean;
  children: (id: string, describedBy: string | undefined) => ReactNode;
}) {
  const id = useId();
  const help = `${id}-help`;
  const has = Boolean(errors?.length);
  return (
    <div className={`field${has ? " invalid" : ""}${wide ? " wide" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {children(id, has || hint ? help : undefined)}
      {(has || hint) && (
        <div id={help} className={has ? "field-error" : "field-hint"}>
          {has ? errors!.join(" ") : hint}
        </div>
      )}
    </div>
  );
}

/**
 * A decimal typed as text and kept as text, so 0.1 stays 0.1: only digits,
 * one point and a leading minus get in.
 */
export function DecimalInput({ value, onChange, places = 4, allowNegative = false, ...rest }: {
  value: string;
  onChange: (value: string) => void;
  places?: number;
  allowNegative?: boolean;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const pattern = new RegExp(`^${allowNegative ? "-?" : ""}\\d*(\\.\\d{0,${places}})?$`);
  return (
    <input
      {...rest}
      inputMode="decimal"
      autoComplete="off"
      value={value}
      onChange={(event) => {
        const next = event.target.value.replace(/,/g, "");
        if (pattern.test(next)) onChange(next);
      }}
    />
  );
}

/** A stored decimal shown without its padding: "10.0000" reads 10. */
export function trimDecimal(value: string): string {
  return value.includes(".") ? value.replace(/\.?0+$/, "") : value;
}

/**
 * A figure changed where it stands and saved when the box is left, if it
 * changed: compared with what was saved, not with what is shown, or a
 * figure the person retyped to the same value would never commit.
 */
export function CommitDecimal({ value, onCommit, places = 4, label }: {
  value: string; onCommit: (value: string) => void; places?: number; label: string;
}) {
  const [typed, setTyped] = useState<string | null>(null);
  const saved = trimDecimal(value);
  return (
    <DecimalInput
      className="cell-input"
      aria-label={label}
      places={places}
      value={typed ?? saved}
      onChange={setTyped}
      onBlur={() => {
        if (typed !== null && typed !== saved) onCommit(typed);
        setTyped(null);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        if (event.key === "Escape") setTyped(null);
      }}
    />
  );
}

/** The same for words: a batch number typed where the line stands. */
export function CommitText({ value, onCommit, label, placeholder }: {
  value: string; onCommit: (value: string) => void; label: string; placeholder?: string;
}) {
  const [typed, setTyped] = useState<string | null>(null);
  return (
    <input
      className="cell-input text"
      aria-label={label}
      placeholder={placeholder}
      autoComplete="off"
      value={typed ?? value}
      onChange={(event) => setTyped(event.target.value)}
      onBlur={() => {
        if (typed !== null && typed.trim() !== value) onCommit(typed.trim());
        setTyped(null);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        if (event.key === "Escape") setTyped(null);
      }}
    />
  );
}

export function today(timeZone = plantTimeZone()): string {
  // The plant's date, as the server counts it, not the browser's: en-CA writes ISO.
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}
