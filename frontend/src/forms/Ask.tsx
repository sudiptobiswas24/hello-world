import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { DecimalInput, Field, today } from "./fields";

/** One box an action asks for before it runs: why, on which day, how much. */
export interface AskField {
  key: string;
  label: string;
  kind?: "text" | "date" | "money";
  hint?: string;
  /** What it starts with: a date starts with today unless given. */
  initial?: string | null;
}

export interface AskSpec {
  /** The dialog's name, read out and found by: "Void the payment". */
  title: string;
  /** What it says above the boxes, if anything. */
  note?: string;
  fields: AskField[];
  /** The button that runs it. */
  submit: string;
  danger?: boolean;
}

/** What was typed, by key: text as typed, a date as YYYY-MM-DD, an amount as exact text; "" left empty. */
export type Answers = Record<string, string>;

/**
 * The questions an action asks before it runs, in a dialog whose every box
 * is a labelled field (O418). They were `window.prompt` calls: a box with
 * no label a screen reader reads blank, that a test cannot find by its
 * label, and that asks two questions as two dialogs one after the other.
 *
 * `ask(spec)` shows it and resolves with the answers, or null when the
 * person says not now; render `dialog` once in the screen.
 */
export function useAsk(): { ask: (spec: AskSpec) => Promise<Answers | null>; dialog: ReactNode } {
  const [open, setOpen] = useState<{ spec: AskSpec; resolve: (answers: Answers | null) => void } | null>(null);
  const ask = useCallback((spec: AskSpec) => new Promise<Answers | null>((resolve) => setOpen({ spec, resolve })), []);
  const finish = (answers: Answers | null) => {
    open?.resolve(answers);
    setOpen(null);
  };
  return { ask, dialog: open ? <AskDialog spec={open.spec} finish={finish} /> : null };
}

function AskDialog({ spec, finish }: { spec: AskSpec; finish: (answers: Answers | null) => void }) {
  const [values, setValues] = useState<Answers>(() => Object.fromEntries(spec.fields.map((field) => [
    field.key, field.initial ?? (field.kind === "date" ? today() : ""),
  ])));
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    form.current?.querySelector<HTMLElement>("input, textarea")?.focus();
  }, []);
  const set = (key: string) => (next: string) => setValues((current) => ({ ...current, [key]: next }));
  return (
    <div className="ask-backdrop" onKeyDown={(event) => { if (event.key === "Escape") finish(null); }}>
      <form ref={form} className="sheet action-form ask" role="dialog" aria-modal="true" aria-label={spec.title}
        onSubmit={(event) => { event.preventDefault(); finish(values); }}>
        <h2>{spec.title}</h2>
        {spec.note && <p className="muted">{spec.note}</p>}
        <div className="field-grid">
          {spec.fields.map((field) => (
            <Field key={field.key} label={field.label} hint={field.hint} wide={!field.kind || field.kind === "text"}>
              {(id, describedBy) => field.kind === "money"
                ? <DecimalInput id={id} aria-describedby={describedBy} places={2} value={values[field.key] ?? ""} onChange={set(field.key)} />
                : <input id={id} aria-describedby={describedBy} type={field.kind === "date" ? "date" : "text"} autoComplete="off"
                    value={values[field.key] ?? ""} onChange={(event) => set(field.key)(event.target.value)} />}
            </Field>
          ))}
        </div>
        <div className="row-actions">
          <button type="submit" className={`btn${spec.danger ? " danger" : " primary"}`}>{spec.submit}</button>
          <button type="button" className="btn" onClick={() => finish(null)}>Not now</button>
        </div>
      </form>
    </div>
  );
}
