import type { ReactNode } from "react";

import { Input, shown, type FieldDef } from "../views/RecordScreen";
import { Field } from "./fields";

type Row = Record<string, unknown>;
type Ref = Row & { id: number };

/**
 * A titled group of boxes on a form: each with what the server said is
 * wrong with it, found under `prefix` when one request carries several
 * sections ("terms.credit_hold_reason").
 */
export function FieldSection({ title, fields, value, set, errors, prefix, editable, refs, children }: {
  title?: string;
  fields: FieldDef[];
  value: Row;
  set: (key: string, next: unknown) => void;
  errors: Record<string, string[]>;
  prefix?: string;
  editable: boolean;
  refs: Record<string, Ref[] | undefined>;
  children?: ReactNode;
}) {
  const at = (key: string) => (prefix ? `${prefix}.${key}` : key);
  const whole = errors[at("non_field_errors")];
  return (
    <section className="form-section" aria-label={title}>
      {title && <h2 className="section-title">{title}</h2>}
      <div className="field-grid">
        {fields.map((field) => (
          <Field key={field.key} label={field.label} hint={field.hint} errors={errors[at(field.key)]}
            wide={field.wide || field.kind === "textarea"}>
            {(id) => editable && !field.readOnly
              ? <Input field={field} value={value[field.key]} set={(next) => set(field.key, next)} id={id}
                  refs={refs[field.key]} invalid={Boolean(errors[at(field.key)])} />
              : <output id={id}>{shown(field, value as Ref, refs[field.key])}</output>}
          </Field>
        ))}
        {children}
      </div>
      {whole && <p className="form-error" role="alert">{whole.join(" ")}</p>}
    </section>
  );
}
