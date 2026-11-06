import { useState } from "react";

import { useAct, useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { Field } from "../../forms/fields";

interface Kind { kind: string; what: string; columns: string[]; needs: string[] }
interface Report {
  kind: string; rows: number; created: number; committed: boolean;
  errors: [number, string, string][]; passwords: [string, string][];
}

const KINDS = "/api/imports/records/";

/**
 * The old system's records, once, at go-live: pick the kind, take its
 * blank file, fill it in the spreadsheet, and bring it back here. Checked
 * whole first; one bad row and nothing is kept. Kept on the second call.
 */
export default function ImportRecords() {
  const { can } = useAccess();
  const kinds = useGet<Kind[]>(KINDS);
  const act = useAct<Report>();
  const [kind, setKind] = useState("");
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [options, setOptions] = useState<Record<string, string>>({ reason: "OPENING" });
  const [report, setReport] = useState<Report | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const chosen = (kinds.data ?? []).find((row) => row.kind === kind);
  const may = can("core.import_records");

  const read = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setText(String(reader.result ?? ""));
      setFileName(file.name);
    };
    reader.readAsText(file, "utf-8");
  };
  const send = async (commit: boolean) => {
    if (commit && !window.confirm(`Keep this ${kind} file? What it makes is kept for good; a mistake is undone by hand.`)) return;
    const outcome = await act.run("POST", `${KINDS}run/`, { kind, text, commit, ...options }, { done: commit ? "Kept" : "Checked" });
    if (outcome.ok) {
      setReport(outcome.data);
      setProblems([]);
    } else {
      setReport(null);
      setProblems(Object.values(outcome.error.fields).flat());
    }
  };
  const set = (key: string, value: string) => setOptions((current) => ({ ...current, [key]: value }));

  return (
    <article className="doc">
      <DocHeader back="/settings/company" backLabel="Settings" title="Bring old records in">
        {may && chosen && <ActionButton pending={act.pending} disabled={!text} onClick={() => void send(false)}>Check</ActionButton>}
        {may && chosen && <ActionButton primary pending={act.pending} disabled={!text} onClick={() => void send(true)}>Keep</ActionButton>}
      </DocHeader>
      <Sheet>
        <p className="muted">
          In the order listed, each kind after the ones it names. A file is checked whole and kept whole: one bad row and nothing from it is kept.
          Dates are YYYY-MM-DD or DD-MM-YYYY; yes-or-no columns take yes or no; codes are the system's own.
        </p>
        <div className="field-grid">
          <Field label="Kind">
            {(id) => (
              <select id={id} value={kind} onChange={(event) => { setKind(event.target.value); setReport(null); }}>
                <option value="">Choose…</option>
                {(kinds.data ?? []).map((row, index) => <option key={row.kind} value={row.kind}>{index + 1}. {row.kind} — {row.what}</option>)}
              </select>
            )}
          </Field>
          {chosen && (
            <Field label="Blank file" hint={`Columns: ${chosen.columns.join(", ")}`}>
              {(id) => <a id={id} className="btn" href={`${KINDS}template/?kind=${chosen.kind}`}>Download {chosen.kind}.csv</a>}
            </Field>
          )}
          {chosen?.needs.includes("date") && (
            <Field label="As at" hint="The day the opening position stands at: go-live">
              {(id) => <input id={id} type="date" value={options.date ?? ""} onChange={(event) => set("date", event.target.value)} />}
            </Field>
          )}
          {chosen?.needs.includes("against") && (
            <Field label="Opening-balance account" hint="Its code, an equity account: 3900">
              {(id) => <input id={id} value={options.against ?? ""} onChange={(event) => set("against", event.target.value)} />}
            </Field>
          )}
          {chosen?.needs.includes("reason") && (
            <Field label="Adjustment reason" hint="Its code; posting to the opening-balance account">
              {(id) => <input id={id} value={options.reason ?? ""} onChange={(event) => set("reason", event.target.value)} />}
            </Field>
          )}
          {chosen?.needs.includes("memo") && (
            <Field label="Memo">
              {(id) => <input id={id} value={options.memo ?? ""} onChange={(event) => set("memo", event.target.value)} />}
            </Field>
          )}
        </div>
        {chosen && (
          <>
            <Field label="The file" hint={fileName ? `${fileName} is loaded below; edit it here or choose another` : "Choose the CSV, or paste it below"}>
              {(id) => <input id={id} type="file" accept=".csv,text/csv,text/plain" onChange={(event) => read(event.target.files?.[0])} />}
            </Field>
            <Field label="Rows" hint="Headings first">
              {(id) => <textarea id={id} rows={12} value={text} onChange={(event) => setText(event.target.value)} style={{ width: "100%", fontFamily: "monospace" }} />}
            </Field>
          </>
        )}
        {problems.length > 0 && <ul className="form-error" role="alert">{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}
        {report && (
          <>
            <p>
              {report.rows} row(s), {report.created} would be made{report.committed ? " and were kept." : report.errors.length ? "; nothing kept." : "; not kept yet."}
            </p>
            {report.errors.length > 0 && (
              <ul className="form-error" role="alert">
                {report.errors.map(([row, column, message]) => <li key={`${row}-${column}-${message}`}>{row ? `Row ${row}` : "File"}{column ? `, ${column}` : ""}: {message}</li>)}
              </ul>
            )}
            {report.passwords.length > 0 && (
              <section className="related">
                <h2 className="section-title">First passwords, shown once</h2>
                <p className="muted">Hand each to its person now; they are not kept anywhere and cannot be shown again.</p>
                <table>
                  <thead><tr><th scope="col">Login</th><th scope="col">First password</th></tr></thead>
                  <tbody>{report.passwords.map(([username, password]) => <tr key={username}><td>{username}</td><td><code>{password}</code></td></tr>)}</tbody>
                </table>
              </section>
            )}
          </>
        )}
      </Sheet>
    </article>
  );
}
