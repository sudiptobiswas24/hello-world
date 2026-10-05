import { useId, type ReactNode } from "react";

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

export function today(timeZone = "Asia/Kolkata"): string {
  // The plant's date, not the browser's idea of Greenwich: en-CA writes ISO.
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}
