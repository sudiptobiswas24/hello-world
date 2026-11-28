import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useAct, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { Field } from "../../forms/fields";
import { money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Statement { id: number; bank_account_name: string; start_date: string; end_date: string; closed: boolean; [key: string]: unknown }
interface Report {
  rows: number; added: number; already_there: number; committed: boolean;
  errors: [number, string, string][]; lines_total: string | null; difference: string | null;
}

/**
 * The bank's export, pasted in: checked whole first, then kept. A row
 * outside the statement's dates or without an amount stops the file with
 * its row number; a row already on the statement is passed over, so the
 * same file twice adds nothing.
 */
export default function StatementImport() {
  const [search] = useSearchParams();
  const id = search.get("statement") ?? undefined;
  const record = useRecord<Statement>("/api/accounting/bank-statements/", id);
  const { can } = useAccess();
  const act = useAct<Report>();
  const [text, setText] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  if (!id) return <div className="empty"><p>Open a bank statement and choose "Import the bank's file".</p></div>;
  if (record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  const statement = record.data;
  if (!statement) return <div className="loading">Opening…</div>;

  const send = async (commit: boolean) => {
    const outcome = await act.run("POST", `/api/accounting/bank-statements/${statement.id}/import_lines/`, { text, commit },
      { done: commit ? "Lines kept" : "Checked" });
    if (outcome.ok) {
      setReport(outcome.data);
      setProblems([]);
    } else {
      setReport(null);
      setProblems(Object.values(outcome.error.fields).flat());
    }
  };
  const may = can("accounting.add_bankstatementline") && !statement.closed;
  return (
    <article className="doc">
      <DocHeader back={`/accounts/bank-statements/${statement.id}`} backLabel="The statement"
        title={`Import the bank's file: ${statement.bank_account_name}, ${statement.start_date} to ${statement.end_date}`}>
        {may && <ActionButton pending={act.pending} onClick={() => void send(false)}>Check</ActionButton>}
        {may && <ActionButton primary pending={act.pending} onClick={() => void send(true)}>Keep the lines</ActionButton>}
      </DocHeader>
      <Sheet>
        {statement.closed && <p className="form-error" role="alert">This statement is closed; reopen it to import into it.</p>}
        <Field label="The file" hint="As the bank's site exports it, headings first: a date, a narration, a reference, and either an amount or a withdrawal and a deposit column">
          {(fid) => <textarea id={fid} rows={12} value={text} onChange={(event) => setText(event.target.value)} style={{ width: "100%", fontFamily: "monospace" }} />}
        </Field>
        {problems.length > 0 && <ul className="form-error" role="alert">{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}
        {report && (
          <>
            <p>
              {report.rows} row(s): {report.added} new, {report.already_there} already on the statement
              {report.committed ? "; kept." : "; not kept yet."}
              {report.lines_total !== null && <> The lines come to {money(report.lines_total)}; against the opening and closing balances that leaves {money(report.difference)}{report.difference === "0.00" ? ": the statement is whole." : " unexplained by the file."}</>}
            </p>
            {report.errors.length > 0 && (
              <ul className="form-error" role="alert">
                {report.errors.map(([row, column, message]) => <li key={`${row}-${column}`}>Row {row}{column ? `, ${column}` : ""}: {message}</li>)}
              </ul>
            )}
            {report.committed && <p><Link to={`/accounts/bank-statements/${statement.id}`}>Back to the statement</Link></p>}
          </>
        )}
      </Sheet>
    </article>
  );
}
