import { useReference } from "../api/hooks";
import { useAccess } from "../auth/me";
import { Field } from "./fields";

export interface CustomField {
  id: number;
  kind: string;
  key: string;
  label: string;
  field_type: "text" | "number" | "date" | "yes_no" | "choice";
  choice_list: string[];
  required: boolean;
  hint: string;
  position: number;
}

export type Extra = Record<string, unknown>;

/** The keeper's fields for one kind of record, read once a session. */
export function useCustomFields(kind: string) {
  const { can } = useAccess();
  return useReference<CustomField>("/api/core/custom-fields/", { kind, is_active: "true" }, can("core.view_customfield"));
}

/**
 * The custom fields of a record, as boxes beside its own: each value in
 * `extra` under the field's key, sent back whole. What a field holds is
 * checked on the server, which answers beside `extra.<key>`.
 */
export function ExtraFields({ kind, value, set, errors, editable }: {
  kind: string;
  value: Extra | null | undefined;
  set: (next: Extra) => void;
  errors: Record<string, string[]>;
  editable: boolean;
}) {
  const fields = useCustomFields(kind);
  const rows = fields.data ?? [];
  if (rows.length === 0) return null;
  const extra = value ?? {};
  const put = (key: string, next: unknown) => set({ ...extra, [key]: next });
  return (
    <div className="field-grid">
      {rows.map((field) => {
        const held = extra[field.key];
        const text = held === null || held === undefined ? "" : String(held);
        return (
          <Field key={field.key} label={field.required ? `${field.label} *` : field.label} hint={editable ? field.hint : undefined}
            errors={errors[`extra.${field.key}`]}>
            {(id) => !editable
              ? <output id={id}>{field.field_type === "yes_no" ? (held === true ? "Yes" : held === false ? "No" : "—") : text || "—"}</output>
              : field.field_type === "yes_no"
                ? <input id={id} type="checkbox" checked={held === true} onChange={(event) => put(field.key, event.target.checked)} />
                : field.field_type === "choice"
                  ? (
                    <select id={id} value={text} onChange={(event) => put(field.key, event.target.value)}>
                      <option value="">—</option>
                      {field.choice_list.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
                    </select>
                  )
                  : <input id={id} type={field.field_type === "date" ? "date" : "text"} inputMode={field.field_type === "number" ? "decimal" : undefined}
                      value={text} onChange={(event) => put(field.key, event.target.value)} />}
          </Field>
        );
      })}
    </div>
  );
}
