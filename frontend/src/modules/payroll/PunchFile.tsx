import { useState } from "react";

import { useAct } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { Field } from "../../forms/fields";

interface Report {
  rows: number;
  created: number;
  updated: number;
  committed: boolean;
  kept: [number, string][];
  to_enter: [number, string][];
  errors: [number, string, string][];
}

/**
 * The reader's punch file, pasted in: checked whole first, then kept.
 * A day entered by hand stays as entered; a row with one punch is listed
 * for the supervisor to enter, not guessed at. Nothing is kept while any
 * row is wrong.
 */
export default function PunchFile() {
  const { can } = useAccess();
  const act = useAct<Report>();
  const [text, setText] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const send = async (commit: boolean) => {
    const outcome = await act.run("POST", "/api/hr/attendance/punches/", { text, commit }, { done: commit ? "Kept" : "Checked" });
    if (outcome.ok) {
      setReport(outcome.data);
      setProblems([]);
    } else {
      setReport(null);
      setProblems(Object.values(outcome.error.fields).flat());
    }
  };
  const may = can("hr.add_attendanceday");

  return (
    <article className="doc">
      <DocHeader back="/payroll/attendance" backLabel="Attendance" title="Punch file">
        {may && <ActionButton pending={act.pending} onClick={() => void send(false)}>Check</ActionButton>}
        {may && <ActionButton primary pending={act.pending} onClick={() => void send(true)}>Keep</ActionButton>}
      </DocHeader>
      <Sheet>
        <Field label="The file" hint="employee_number, date, shift, in, out: one row a person a day, as the reader exports it">
          {(id) => <textarea id={id} rows={12} value={text} onChange={(event) => setText(event.target.value)} style={{ width: "100%", fontFamily: "monospace" }} />}
        </Field>
        {problems.length > 0 && <ul className="form-error" role="alert">{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}
        {report && (
          <>
            <p>{report.rows} row(s): {report.created} day(s) new, {report.updated} changed{report.committed ? ", kept." : "; not kept yet."}</p>
            {report.errors.length > 0 && (
              <ul className="form-error" role="alert">
                {report.errors.map(([row, column, message]) => <li key={`${row}-${column}`}>Row {row}{column ? `, ${column}` : ""}: {message}</li>)}
              </ul>
            )}
            {report.to_enter.length > 0 && (
              <>
                <h2>To enter by hand</h2>
                <ul>{report.to_enter.map(([row, message]) => <li key={row}>Row {row}: {message}</li>)}</ul>
              </>
            )}
            {report.kept.length > 0 && (
              <>
                <h2>Kept as entered</h2>
                <ul>{report.kept.map(([row, message]) => <li key={row}>Row {row}: {message}</li>)}</ul>
              </>
            )}
          </>
        )}
      </Sheet>
    </article>
  );
}
