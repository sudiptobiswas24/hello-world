import type { ReactNode } from "react";
import { Link } from "react-router";

import { money } from "../lib/format";

/** The top of a document: where it sits, what it is, its state, what can be done. */
export function DocHeader({ back, backLabel, title, number, state, tone, children }: {
  back: string;
  backLabel: string;
  title: string;
  number?: string | null;
  state?: string;
  tone?: string;
  children?: ReactNode;
}) {
  return (
    <header className="doc-head">
      <nav className="doc-crumbs" aria-label="Breadcrumb">
        <Link to={back}>{backLabel}</Link>
        <span aria-hidden="true">›</span>
        <span>{number || title}</span>
      </nav>
      <div className="doc-title">
        <h1>{number || title}</h1>
        {state && <span className={`pill pill-${tone ?? "draft"}`}>{state}</span>}
        <div className="doc-actions">{children}</div>
      </div>
    </header>
  );
}

/** The steps a document goes through, with where it is now. */
export function Steps({ steps, at }: { steps: string[]; at: number }) {
  return (
    <ol className="steps" aria-label="Progress">
      {steps.map((step, index) => (
        <li key={step} className={index < at ? "done" : index === at ? "now" : undefined} aria-current={index === at ? "step" : undefined}>
          {step}
        </li>
      ))}
    </ol>
  );
}

export function Sheet({ children }: { children: ReactNode }) {
  return <div className="sheet">{children}</div>;
}

export function Totals({ rows }: { rows: [string, string | null | undefined, boolean?][] }) {
  return (
    <dl className="totals">
      {rows.map(([label, value, strong]) => (
        <div key={label} className={strong ? "strong" : undefined}>
          <dt>{label}</dt>
          <dd>{money(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A button that runs an action, held while it does. */
export function ActionButton({ onClick, pending, primary, danger, children, disabled, title }: {
  onClick: () => void;
  pending?: boolean;
  primary?: boolean;
  danger?: boolean;
  disabled?: boolean;
  title?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`btn${primary ? " primary" : ""}${danger ? " danger" : ""}`}
      disabled={pending || disabled}
      aria-busy={pending || undefined}
      title={title}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
