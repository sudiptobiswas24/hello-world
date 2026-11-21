import { useState } from "react";

import { useAct } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { today } from "../../forms/fields";
import { date, money } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { employee: number; to_pay: string | null; on_pay: string | null; paid: string };
interface Statement {
  year: number; name: string; percent: string; declaration: number | null; declared_on: string | null;
  to: string; pay_by: string; rows: Row[]; note: string;
  total: { wages: string; owed: string; accrued: string; difference: string; paid: string };
}

const DECLARATIONS = "/api/hr/bonus-declarations/";
// The accounting year runs from 1 April: the year that has just closed is the one a bonus is declared on.
const THIS_YEAR = Number(today().slice(0, 4)) - (Number(today().slice(5, 7)) >= 4 ? 0 : 1);
const AS_DECLARED = "declared";

const PARAMS: ParamDef[] = [
  { key: "year", label: "Accounting year", kind: "choice", required: true, initial: String(THIS_YEAR - 1),
    choices: [THIS_YEAR - 1, THIS_YEAR, THIS_YEAR - 2, THIS_YEAR - 3].map((y) => [String(y), `${y}-${String((y + 1) % 100).padStart(2, "0")}`]) },
  { key: "percent", label: "At", kind: "choice", required: true, initial: AS_DECLARED,
    choices: [[AS_DECLARED, "As declared, or the least"],
      ...["8.33", "9", "10", "11", "12", "12.5", "13", "14", "15", "16", "17", "18", "19", "20"].map((p): [string, string] => [p, `${p}%`])] },
];

/** What can be done with the year: declare it, put it on a month's pay, take it off, void it. */
function Actions({ data }: { data: Statement }) {
  const { can } = useAccess();
  const act = useAct();
  const [month, setMonth] = useState(today().slice(0, 7));
  const percent = String(Number(data.percent));
  const waiting = data.rows.filter((row) => Number(row.to_pay ?? 0) > 0 && !row.on_pay).length;
  const onPay = data.rows.filter((row) => row.on_pay && Number(row.paid) === 0).length;
  // One click, as a pay run is posted: the line beside it says what it books, and it is voided, never changed.
  const declare = () =>
    act.run("POST", DECLARATIONS, { year: data.year, percent: data.percent }, { done: `Declared at ${percent}%` });
  const on = (action: string, done: string, body?: unknown) =>
    act.run("POST", `${DECLARATIONS}${data.declaration}/${action}/`, body ?? {}, { done });
  return (
    <p className="actions">
      {data.note ? data.note : data.declaration === null
        ? <>Not declared: owed {money(data.total.owed)} at {percent}%, accrued {money(data.total.accrued)}, {money(data.total.difference)} to book. To be paid by {date(data.pay_by)}.{" "}
          {can("hr.add_bonusdeclaration") && data.rows.length > 0 && data.to < today() && (
            <ActionButton primary pending={act.pending} onClick={() => void declare()}>Declare at {percent}%</ActionButton>)}</>
        : <>Declared on {date(data.declared_on!)} at {percent}%: owed {money(data.total.owed)}, paid {money(data.total.paid)}. To be paid by {date(data.pay_by)}.{" "}
          {waiting > 0 && can("hr.add_employeecompensation") && (
            <>
              <label className="inline">On the pay of{" "}
                <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
              </label>{" "}
              <ActionButton primary pending={act.pending} onClick={() => void on("put_on_pay", "On that month's pay", { month: `${month}-01` })}>Put on pay</ActionButton>
            </>)}
          {onPay > 0 && can("hr.delete_employeecompensation") && (
            <ActionButton pending={act.pending} onClick={() => void on("take_off_pay", "Taken off pay")}>Take off pay</ActionButton>)}
          {can("hr.void_bonusdeclaration") && (
            <ActionButton pending={act.pending} onClick={() => void on("void", "Voided")}>Void the declaration</ActionButton>)}</>}
    </p>
  );
}

/** The year's statutory bonus: who is owed it, on what wages, what was accrued and what has been paid. */
export default function Bonus() {
  return (
    <ReportView<Statement, Row>
      title="Bonus"
      endpoint="/api/hr/bonus-statement/"
      params={PARAMS}
      send={(values) => (values.percent === AS_DECLARED ? { year: values.year } : { year: values.year, percent: values.percent })}
      rows={(data) => data.rows.map((row) => ({ ...row, id: row.employee }))}
      above={(data) => <Actions data={data} />}
      columns={[
        { key: "number", label: "No.", width: "6rem" },
        { key: "name", label: "Name" },
        { key: "days", label: "Days worked", width: "7rem" },
        { key: "wages", label: "Wages counted", kind: "money", width: "9rem" },
        { key: "owed", label: "Bonus owed", kind: "money", width: "9rem" },
        { key: "accrued", label: "Accrued", kind: "money", width: "9rem" },
        { key: "difference", label: "Difference", kind: "money", width: "9rem" },
        { key: "paid", label: "Paid", kind: "money", width: "8rem" },
        { key: "why", label: "", render: (row) => String(row.why || (row.on_pay ? `On the pay of ${date(String(row.on_pay))}` : "")) },
      ]}
      foot={(_rows, data) => ["Total", null, null, money(data.total.wages), money(data.total.owed), money(data.total.accrued),
        money(data.total.difference), money(data.total.paid), null]}
      empty="No pay run was posted for that year."
    />
  );
}
