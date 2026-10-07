import { useQueries } from "@tanstack/react-query";
import { Trail } from "./Trail";
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { list, type Query } from "../api/client";
import { useAct, useGet, useRecord } from "../api/hooks";
import { useAccess } from "../auth/me";
import { ActionButton, DocHeader, Sheet } from "../forms/Document";
import { DecimalInput, Field, today } from "../forms/fields";
import { RecordPicker } from "../forms/RecordPicker";
import { useDraft } from "../forms/useDraft";
import { date, money, quantity } from "../lib/format";
import { ErrorPanel } from "../shell/ErrorPanel";
import { DataTable, type Column } from "./DataTable";

type Row = Record<string, unknown> & { id: number };

export type FieldKind =
  | "text" | "textarea" | "decimal" | "money" | "integer" | "date" | "time" | "bool" | "choice" | "ref" | "pick";

/**
 * One box on a record. `ref` is a short master read whole (machines, work
 * centres, shifts) and shown by name; `pick` is a long one searched as
 * typed (items, parties, employees).
 */
export interface FieldDef {
  key: string;
  label: string;
  kind?: FieldKind;
  hint?: string;
  choices?: [string, string][];
  ref?: { endpoint: string; label: (row: Row) => string; query?: Query; permission?: string };
  pick?: { endpoint: string; label: (row: Row) => string; detail?: (row: Row) => string; query?: Query; permission?: string };
  /** Decimal places a decimal box takes. */
  places?: number;
  /** A decimal that may be less than nothing (a write-off). */
  negative?: boolean;
  /** Shown, never sent: the server works it out. */
  readOnly?: boolean;
  /** Set when the record is made and fixed after. */
  createOnly?: boolean;
  /** Left out of the new form. */
  existingOnly?: boolean;
  /** Asked only when the record is made, and not shown after (it was turned into something else). */
  newOnly?: boolean;
  wide?: boolean;
  /** What a new record starts with. */
  initial?: unknown;
  /** How it reads when it cannot be changed, if not the plain value. */
  show?: (record: Row) => ReactNode;
}

/**
 * Something done to the record that is not an edit: complete a job, void
 * a reading. Its own fields are asked in a small form under the header.
 */
export interface ActionDef {
  label: string;
  /** The action's path under the record: "complete" posts to {id}/complete/. */
  path: string;
  permission: string;
  when?: (record: Row) => boolean;
  /** Or worked out from the record: a statement's match offers its own lines. */
  fields?: FieldDef[] | ((record: Row) => FieldDef[]);
  done: string;
  primary?: boolean;
  danger?: boolean;
  /** Where it posts, when not under the record (a repair raised on a stoppage). */
  url?: (record: Row) => string;
  /** What it sends, when not its fields as they are. */
  body?: (values: Row, record: Row) => unknown;
  /** Where to go after, given what the server answered. */
  then?: (result: Row) => string;
}

/** A page a record opens in a new tab, served by the API under the record's own read permission. */
export interface LinkDef {
  label: string;
  href: (record: Row) => string;
  when?: (record: Row) => boolean;
  /** Another screen of the application, opened here; otherwise a file (a PDF) in a new tab. */
  same?: boolean;
}

/** Something done to one row of a panel: return these spares. */
export interface RowAction {
  label: string;
  permission: string;
  url: (row: Row, record: Row) => string;
  when?: (row: Row) => boolean;
  done: string;
  /** Asked first, in words, when the action cannot be taken back. */
  confirm?: string;
  /** A change to the row itself (archive it) rather than an action posted to it. */
  method?: "POST" | "PATCH";
  body?: (row: Row) => unknown;
}

/** The rows that hang off the record: a job's labour, a meter's readings. */
export interface PanelDef {
  title: string;
  permission: string;
  endpoint: string;
  query: (record: Row) => Query;
  columns: Column<Row>[];
  href?: (row: Row) => string;
  /** Rows straight off the record itself, not another endpoint. */
  rows?: (record: Row) => Row[];
  /** Rows out of a report about the record, at a path naming it: a batch's trace. */
  read?: { path: (record: Row) => string; rows: (data: unknown) => Row[] };
  rowAction?: RowAction;
  /** Several, where a row can be made primary, archived or restored. */
  rowActions?: RowAction[];
  /** A line added from the panel, while the record allows it. */
  adder?: {
    label: string;
    permission: string;
    url: (record: Row) => string;
    /** Or worked out from the record: a reading's choices are its own plan's lines. */
    fields: FieldDef[] | ((record: Row) => FieldDef[]);
    body: (values: Row, record: Row) => unknown;
    when?: (record: Row) => boolean;
  };
  /** A line taken off, while the record allows it. */
  remover?: { permission: string; url: (row: Row) => string; when?: (record: Row) => boolean };
}

export interface RecordScreenProps {
  endpoint: string;
  back: string;
  backLabel: string;
  /** "New maintenance schedule" */
  newTitle: string;
  heading: (record: Row) => string;
  state?: (record: Row) => { label: string; tone: string } | null;
  fields: FieldDef[];
  actions?: ActionDef[];
  /** Pages the record opens in a new tab: its PDF, a printed card. */
  links?: LinkDef[];
  /** The record's kind as app.model, for its attachments and history. */
  trail?: string;
  panels?: PanelDef[];
  permissions: { add?: string; change?: string; delete?: string };
  /** Whether the record may still be edited in its present state. */
  editable?: (record: Row) => boolean;
  /** Where to go after a delete, and after a create (default: the new record). */
  afterCreate?: (record: Row) => string;
  /** Where a new one is posted, when an action makes it rather than the collection. */
  createUrl?: string;
  note?: (record: Row | undefined) => ReactNode;
  /** Below the panels: an editor the record shares with a document (its trade lines). */
  below?: (record: Row, editable: boolean) => ReactNode;
}

function blank(fields: FieldDef[]): Row {
  const row: Row = { id: 0 };
  for (const field of fields) {
    if (field.readOnly || field.existingOnly) continue;
    row[field.key] = field.initial !== undefined ? field.initial
      : field.kind === "bool" ? false
      : field.kind === "date" && field.initial === undefined ? null
      : field.kind === "ref" || field.kind === "pick" || field.kind === "decimal" || field.kind === "money" || field.kind === "integer" ? null
      : field.kind === "choice" ? field.choices?.[0]?.[0] ?? ""
      : "";
  }
  return row;
}

/** A record's value as words, when it cannot be changed. */
export function shown(field: FieldDef, record: Row, refs: Row[] | undefined): ReactNode {
  if (field.show) return field.show(record);
  const value = record[field.key];
  if (value === null || value === undefined || value === "") return "—";
  switch (field.kind) {
    case "money": return money(value as string);
    case "decimal": return quantity(value as string, field.places ?? 4);
    case "date": return date(value as string);
    case "bool": return value ? "Yes" : "No";
    case "choice": return field.choices?.find(([key]) => key === value)?.[1] ?? String(value);
    case "ref": {
      const row = refs?.find((r) => r.id === value);
      return row && field.ref ? field.ref.label(row) : `#${String(value)}`;
    }
    default: return String(value);
  }
}

/** One box, editable. `refs` is the short master already read for a `ref`. */
export function Input({ field, value, set, id, refs, invalid }: {
  field: FieldDef; value: unknown; set: (next: unknown) => void; id: string; refs?: Row[]; invalid?: boolean;
}) {
  switch (field.kind) {
    case "textarea":
      return <textarea id={id} rows={3} value={String(value ?? "")} onChange={(e) => set(e.target.value)} />;
    case "decimal":
    case "money":
    case "integer":
      return (
        <DecimalInput id={id} places={field.kind === "integer" ? 0 : field.places ?? (field.kind === "money" ? 2 : 4)}
          allowNegative={field.negative}
          value={value === null || value === undefined ? "" : String(value)}
          onChange={(next) => set(next === "" ? null : next)} />
      );
    case "date":
      return <input id={id} type="date" value={String(value ?? "")} onChange={(e) => set(e.target.value || null)} />;
    case "time":
      return <input id={id} type="time" value={String(value ?? "").slice(0, 5)} onChange={(e) => set(e.target.value || null)} />;
    case "bool":
      return <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => set(e.target.checked)} />;
    case "choice":
      return (
        <select id={id} value={String(value ?? "")} onChange={(e) => set(e.target.value)}>
          {field.choices?.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      );
    case "ref":
      return (
        <select id={id} value={value === null || value === undefined ? "" : String(value)}
          onChange={(e) => set(e.target.value ? Number(e.target.value) : null)}>
          <option value="">—</option>
          {(refs ?? []).map((row) => <option key={row.id} value={row.id}>{field.ref!.label(row)}</option>)}
        </select>
      );
    case "pick":
      return (
        <RecordPicker<Row> id={id} endpoint={field.pick!.endpoint} value={(value as number | null) ?? null}
          onChange={(next) => set(next)} label={field.pick!.label} detail={field.pick!.detail}
          fixed={field.pick!.query} invalid={invalid} ariaLabel={field.label} />
      );
    default:
      return <input id={id} value={String(value ?? "")} autoComplete="off" onChange={(e) => set(e.target.value)} />;
  }
}

/**
 * The short masters a set of fields reads whole, each asked only of
 * someone who may read it.
 */
export function useRefs(fields: FieldDef[]): Record<string, Row[] | undefined> {
  const { can } = useAccess();
  const wanted = fields.filter((field) => field.kind === "ref" && field.ref);
  const reads = useQueries({
    queries: wanted.map((field) => ({
      queryKey: ["reference", field.ref!.endpoint, field.ref!.query],
      queryFn: async ({ signal }: { signal: AbortSignal }) =>
        (await list<Row>(field.ref!.endpoint, { page_size: 500, ...field.ref!.query }, signal)).rows,
      staleTime: 10 * 60_000,
      enabled: !field.ref!.permission || can(field.ref!.permission),
    })),
  });
  const out: Record<string, Row[] | undefined> = {};
  wanted.forEach((field, index) => { out[field.key] = reads[index]?.data; });
  return out;
}

/** The small form an action asks before it runs. */
function ActionForm({ action, record, endpoint, onClose }: {
  action: ActionDef; record: Row; endpoint: string; onClose: () => void;
}) {
  const fields = (typeof action.fields === "function" ? action.fields(record) : action.fields) ?? [];
  const refs = useRefs(fields);
  const [values, setValues] = useState<Row>(() => {
    const start = blank(fields);
    for (const field of fields) if (field.kind === "date" && field.initial === undefined) start[field.key] = today();
    return start;
  });
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const act = useAct<Row>();
  const navigate = useNavigate();
  const run = async () => {
    const plain: Record<string, unknown> = {};
    for (const field of fields) plain[field.key] = values[field.key];
    const body = action.body ? action.body(values, record) : plain;
    const url = action.url ? action.url(record) : `${endpoint}${record.id}/${action.path}/`;
    const outcome = await act.run("POST", url, body, { done: action.done });
    if (outcome.ok) {
      onClose();
      if (action.then) navigate(action.then(outcome.data));
    } else setErrors(outcome.error.fields);
  };
  return (
    <form className="sheet action-form" aria-label={action.label} onSubmit={(event) => { event.preventDefault(); void run(); }}>
      <h2>{action.label}</h2>
      <div className="field-grid">
        {fields.map((field) => (
          <Field key={field.key} label={field.label} hint={field.hint} errors={errors[field.key]} wide={field.wide}>
            {(id) => <Input field={field} id={id} value={values[field.key]} refs={refs[field.key]}
              set={(next) => setValues((current) => ({ ...current, [field.key]: next }))} />}
          </Field>
        ))}
      </div>
      {errors.non_field_errors && <p className="form-error" role="alert">{errors.non_field_errors.join(" ")}</p>}
      <div className="row-actions">
        <button type="submit" className={`btn${action.danger ? " danger" : " primary"}`} disabled={act.pending}>{action.label}</button>
        <button type="button" className="btn" onClick={onClose}>Not now</button>
      </div>
    </form>
  );
}

/**
 * One master record or simple document: read, made, changed, and the
 * actions its state allows. Only what the person changed is sent, and
 * what the server refuses is shown beside the box it is about.
 */
function ReadTable({ read, record, columns, href }: {
  read: NonNullable<PanelDef["read"]>; record: Row; columns: Column<Row>[]; href?: (row: Row) => string;
}) {
  const found = useGet<unknown>(read.path(record));
  if (found.isError) return <p className="muted">Could not read it: {found.error.message}</p>;
  return <DataTable rows={found.data === undefined ? [] : read.rows(found.data)} pending={found.isPending}
    columns={columns} href={href} empty="None." />;
}

export function RecordScreen(props: RecordScreenProps) {
  const { endpoint, back, backLabel, newTitle, heading, state, fields, actions = [], links = [], trail, panels = [], permissions, editable, afterCreate, note, below, createUrl } = props;
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Row>(endpoint, id);
  const saved = record.data;
  const draft = useDraft<Row>(isNew ? blank(fields) : saved);
  const refs = useRefs(fields);
  const act = useAct<Row>();
  const [asking, setAsking] = useState<ActionDef | null>(null);

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !saved) return <div className="loading">Opening…</div>;

  const mayEdit = isNew ? Boolean(permissions.add && can(permissions.add))
    : Boolean(permissions.change && can(permissions.change)) && (!editable || editable(saved!));
  const value = draft.value;
  // A picker reads its list; whoever may not read it sees the value, not a box.
  const open = (field: FieldDef) =>
    mayEdit && !field.readOnly && !(field.createOnly && !isNew)
    && !(field.pick?.permission && !can(field.pick.permission));

  const save = async () => {
    const body: Record<string, unknown> = {};
    const source = isNew ? value : draft.changes;
    for (const field of fields) {
      if (field.readOnly || (isNew && field.existingOnly)) continue;
      if (field.key in source) body[field.key] = source[field.key];
    }
    const outcome = isNew
      ? await act.run("POST", createUrl ?? endpoint, body, { done: "Saved" })
      : await act.run("PATCH", `${endpoint}${saved!.id}/`, body, { done: "Saved" });
    if (outcome.ok) {
      draft.reset();
      if (isNew) navigate(afterCreate ? afterCreate(outcome.data) : `${back}/${outcome.data.id}`, { replace: true });
    } else draft.failed(outcome.error);
  };
  const remove = async () => {
    if (!window.confirm("Delete this for good?")) return;
    const outcome = await act.run("DELETE", `${endpoint}${saved!.id}/`, undefined, { done: "Deleted" });
    if (outcome.ok) navigate(back, { replace: true });
  };
  const status = saved && state ? state(saved) : null;
  const offered = saved ? actions.filter((a) => can(a.permission) && (!a.when || a.when(saved))) : [];

  return (
    <article className="doc">
      <DocHeader back={back} backLabel={backLabel} title={newTitle}
        number={saved ? heading(saved) : undefined} state={status?.label} tone={status?.tone}>
        {mayEdit && (isNew || draft.dirty) && (
          <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Create" : "Save"}</ActionButton>
        )}
        {offered.map((action) => (
          <ActionButton key={action.path} primary={action.primary} danger={action.danger} pending={act.pending}
            onClick={() => (typeof action.fields === "function" || action.fields?.length ? setAsking(action) : void act.run(
              "POST", action.url ? action.url(saved!) : `${endpoint}${saved!.id}/${action.path}/`,
              action.body ? action.body({ id: 0 }, saved!) : {},
              { done: action.done, onDone: (result) => { if (action.then) navigate(action.then(result)); } }))}>
            {action.label}
          </ActionButton>
        ))}
        {saved && links.filter((link) => !link.when || link.when(saved)).map((link) => link.same
          ? <Link key={link.label} className="btn" to={link.href(saved)}>{link.label}</Link>
          : <a key={link.label} className="btn" href={link.href(saved)} target="_blank" rel="noopener">{link.label}</a>)}
        {saved && permissions.delete && can(permissions.delete) && (!editable || editable(saved)) && (
          <ActionButton danger pending={act.pending} onClick={() => void remove()}>Delete</ActionButton>
        )}
      </DocHeader>
      {note?.(saved)}
      {asking && saved && (
        <ActionForm action={asking} record={saved} endpoint={endpoint} onClose={() => setAsking(null)} />
      )}
      <Sheet>
        <div className="field-grid">
          {fields.filter((field) => !(isNew && (field.existingOnly || field.readOnly)) && !(!isNew && field.newOnly)).map((field) => (
            <Field key={field.key} label={field.label} hint={open(field) ? field.hint : undefined}
              errors={draft.errors[field.key]} wide={field.wide || field.kind === "textarea"}>
              {(fid) => open(field)
                ? <Input field={field} id={fid} value={value[field.key]} refs={refs[field.key]}
                    invalid={Boolean(draft.errors[field.key])}
                    set={(next) => draft.set(field.key, next as never)} />
                : <output id={fid}>{shown(field, (saved ?? value) as Row, refs[field.key])}</output>}
            </Field>
          ))}
        </div>
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}
      </Sheet>
      {saved && panels.map((panel) => <RecordPanel key={panel.title} panel={panel} record={saved} />)}
      {saved && below?.(saved, Boolean(permissions.change && can(permissions.change)) && (!editable || editable(saved)))}
      {saved && trail && <Trail model={trail} id={saved.id} />}
    </article>
  );
}

/** Rows that hang off a record, on a record screen or any page about one (a party's addresses). */
export function RecordPanel({ panel, record }: { panel: PanelDef; record: Row }) {
  const { can } = useAccess();
  const act = useAct<Row>();
  const [adding, setAdding] = useState(false);
  if (!can(panel.permission)) return null;
  const rowActions = [...(panel.rowAction ? [panel.rowAction] : []), ...(panel.rowActions ?? [])]
    .filter((action) => can(action.permission));
  const remover = panel.remover && can(panel.remover.permission) && (!panel.remover.when || panel.remover.when(record))
    ? panel.remover : null;
  const adder = panel.adder && can(panel.adder.permission) && (!panel.adder.when || panel.adder.when(record))
    ? panel.adder : null;
  const extra: Column<Row>[] = [];
  if (rowActions.length) {
    extra.push({
      key: "_act", label: "", width: rowActions.length > 1 ? "14rem" : "8rem",
      render: (row) => (
        <span className="row-actions">
          {rowActions.filter((action) => !action.when || action.when(row)).map((action) => (
            <button key={action.label} type="button" className="btn" disabled={act.pending} onClick={() => {
              if (action.confirm && !window.confirm(action.confirm)) return;
              void act.run(action.method ?? "POST", action.url(row, record), action.body ? action.body(row) : {},
                { done: action.done });
            }}>{action.label}</button>
          ))}
        </span>
      ),
    });
  }
  if (remover) {
    extra.push({
      key: "_remove", label: "", width: "3rem",
      render: (row) => (
        <button type="button" className="icon-btn" aria-label="Remove the line" disabled={act.pending}
          onClick={() => void act.run("DELETE", remover.url(row), undefined, { done: "Removed" })}>×</button>
      ),
    });
  }
  const columns = [...panel.columns, ...extra];
  return (
    <section className="related">
      <h2 className="section-title">{panel.title}</h2>
      {panel.rows
        ? <DataTable rows={panel.rows(record)} columns={columns} href={panel.href} empty="None yet." />
        : panel.read
          ? <ReadTable read={panel.read} record={record} columns={columns} href={panel.href} />
          : <DataTable endpoint={panel.endpoint} query={panel.query(record)} columns={columns} href={panel.href} empty="None yet." />}
      {adder && (adding
        ? <ActionForm record={record} endpoint="" onClose={() => setAdding(false)} action={{
            label: adder.label, path: "", permission: adder.permission, done: "Added",
            fields: typeof adder.fields === "function" ? adder.fields(record) : adder.fields,
            url: adder.url, body: adder.body,
          }} />
        : <button type="button" className="btn" onClick={() => setAdding(true)}>{adder.label}</button>)}
    </section>
  );
}
