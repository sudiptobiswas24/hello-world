import { useNavigate, useParams } from "react-router";

import { useState } from "react";

import { useAct, usePage, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet, Steps, Totals } from "../../forms/Document";
import { Field } from "../../forms/fields";
import { Pager } from "../../forms/Pager";
import { useDraft } from "../../forms/useDraft";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { Trail } from "../../views/Trail";

interface Run {
  id: number;
  number: string;
  name: string;
  period_start: string;
  period_end: string;
  pay_date: string;
  status: "draft" | "calculated" | "posted" | "voided";
  gross: string;
  net: string;
  employer_cost: string;
  unpaid_net: string;
  [key: string]: unknown;
}

interface Slip {
  id: number;
  employee: string;
  name: string;
  gross: string;
  deductions: string;
  net: string;
  employer_cost: string;
  paid: boolean;
}

const ENDPOINT = "/api/hr/pay-runs/";
const AT: Record<string, number> = { draft: 0, calculated: 1, posted: 2 };

/**
 * A month's pay: worked out from each person's pay and the days they
 * were owed it, checked slip by slip, then posted to the ledger. Whoever
 * works it out does not post it; a posted run is undone by voiding it,
 * never by an edit.
 */
export default function PayRunForm() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Run>(ENDPOINT, id);
  const run = record.data;
  const draft = useDraft<Run>(isNew ? ({ name: "", period_start: "", period_end: "", pay_date: "" } as unknown as Run) : run);
  const [slipPage, setSlipPage] = useState(1);
  const slips = usePage<Slip>("/api/hr/payslips/", { run: run?.id }, slipPage, 100, Boolean(run) && can("hr.view_payslip"));
  const act = useAct<Run>();

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !run) return <div className="loading">Opening…</div>;
  const value = draft.value;
  const editable = isNew || (run!.status === "draft" && can("hr.change_payrun"));

  const save = async () => {
    const outcome = isNew
      ? await act.run("POST", ENDPOINT, { name: value.name, period_start: value.period_start, period_end: value.period_end, pay_date: value.pay_date }, { done: "Pay run opened" })
      : await act.run("PATCH", `${ENDPOINT}${run!.id}/`, draft.changes, { done: "Saved" });
    if (!outcome.ok) return draft.failed(outcome.error);
    draft.reset();
    if (isNew) navigate(`/payroll/runs/${outcome.data.id}`, { replace: true });
  };
  const run_ = (action: string, done: string, body?: unknown) =>
    act.run("POST", `${ENDPOINT}${run!.id}/${action}/`, body ?? {}, { done });

  const field = (key: "name" | "period_start" | "period_end" | "pay_date", label: string, type = "date") => (
    <Field label={label} errors={draft.errors[key]}>
      {(fid) => editable
        ? <input id={fid} type={type} value={String(value[key] ?? "")} onChange={(e) => draft.set(key, e.target.value as never)} />
        : <output id={fid}>{type === "date" ? date(run?.[key] as string) : String(run?.[key] ?? "")}</output>}
    </Field>
  );

  return (
    <article className="doc">
      <DocHeader back="/payroll/runs" backLabel="Pay runs" title="New pay run" number={run ? run.number || run.name : undefined}
        state={run?.status} tone={run?.status === "posted" ? "done" : run?.status === "voided" ? "cancelled" : "draft"}>
        {editable && (draft.dirty || isNew) && <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Open run" : "Save"}</ActionButton>}
        {run && ["draft", "calculated"].includes(run.status) && !draft.dirty && can("hr.change_payrun") && (
          <ActionButton primary={run.status === "draft"} pending={act.pending} onClick={() => void run_("calculate", "Worked out")}>
            {run.status === "draft" ? "Work it out" : "Work it out again"}
          </ActionButton>
        )}
        {run?.status === "calculated" && can("hr.post_payrun") && (
          <ActionButton primary pending={act.pending} onClick={() => void run_("post", "Posted to the ledger")}>Post</ActionButton>
        )}
        {run?.status === "posted" && can("hr.view_payslip") && (
          <>
            <a className="btn" href={`${ENDPOINT}${run.id}/ecr/`}>PF ECR file</a>
            <a className="btn" href={`${ENDPOINT}${run.id}/esi/`}>ESI file</a>
          </>
        )}
        {run?.status === "posted" && can("hr.post_payrun") && (
          <ActionButton danger pending={act.pending} onClick={() => {
            const day = window.prompt("Void as of which date? (YYYY-MM-DD, blank for today)");
            if (day !== null) void run_("void", "Voided", day ? { on_date: day } : {});
          }}>Void</ActionButton>
        )}
      </DocHeader>
      {run && run.status !== "voided" && <Steps steps={["Opened", "Worked out", "Posted"]} at={AT[run.status] ?? 0} />}

      <Sheet>
        <div className="field-grid">
          {field("name", "Name", "text")}
          {field("period_start", "For work from")}
          {field("period_end", "To")}
          {field("pay_date", "Paid on")}
        </div>
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}

        {run && run.status !== "draft" && (
          <>
            <div className="lines">
              <table>
                <thead><tr><th scope="col">Person</th><th scope="col" className="k-money">Gross</th><th scope="col" className="k-money">Deductions</th><th scope="col" className="k-money">Net</th><th scope="col" className="k-money">Employer cost</th><th scope="col">Paid</th></tr></thead>
                <tbody>
                  {(slips.data?.rows ?? []).map((slip) => (
                    <tr key={slip.id}>
                      <td><strong>{slip.employee}</strong> {slip.name}</td>
                      <td className="k-money">{money(slip.gross)}</td>
                      <td className="k-money">{money(slip.deductions)}</td>
                      <td className="k-money">{money(slip.net)}</td>
                      <td className="k-money">{money(slip.employer_cost)}</td>
                      <td>{slip.paid ? "Yes" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {slips.data && <Pager page={slipPage} size={100} total={slips.data.total} onPage={setSlipPage} />}
            </div>
            <Totals rows={[["Gross", run.gross], ["Net", run.net, true], ["Employer cost", run.employer_cost], ["Still to pay", run.unpaid_net]]} />
          </>
        )}
      </Sheet>
      {run && <Trail model="hr.payrun" id={run.id} />}
    </article>
  );
}
