import { useState } from "react";
import { useNavigate, useParams } from "react-router";

import { useAct, useRecord, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { Field } from "../../forms/fields";
import { useDraft } from "../../forms/useDraft";
import { date, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Leave {
  id: number;
  employee: number | null;
  employee_name: string;
  policy: number | null;
  policy_name: string;
  leave_type: string;
  start_date: string;
  end_date: string;
  half_day: boolean;
  days_taken: string | null;
  status: string;
  reason: string;
  decided_by: number | null;
  decided_by_name: string;
  [key: string]: unknown;
}

interface Policy { id: number; code: string; name: string; leave_type: string }
interface Employee { id: number; employee_number: string; name: string }

const ENDPOINT = "/api/hr/leave-requests/";
const TYPES: [string, string][] = [["vacation", "Vacation"], ["sick", "Sick"], ["unpaid", "Unpaid"], ["other", "Other"]];
const TONES: Record<string, "draft" | "open" | "done" | "info"> = {
  pending: "open", approved: "done", rejected: "draft", cancelled: "draft",
};

/**
 * One leave request: asked for by the person (or by HR for them), and
 * decided by their manager. Who may decide is the server's rule, said
 * in words when it refuses; the buttons only follow the permission.
 */
export default function LeaveForm() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { me, can } = useAccess();
  const record = useRecord<Leave>(ENDPOINT, id);
  const leave = record.data;
  const policies = useReference<Policy>("/api/hr/leave-policies/");
  // Anyone else's is HR's to ask for; the server refuses otherwise.
  const forOthers = can("hr.change_employee");
  const employees = useReference<Employee>("/api/hr/employees/", undefined, isNew && forOthers);
  const blank = {
    employee: me.employee ?? null, policy: null, leave_type: "vacation", start_date: "", end_date: "",
    half_day: false, reason: "",
  } as unknown as Leave;
  const draft = useDraft<Leave>(isNew ? blank : leave);
  const act = useAct<Leave>();
  const [why, setWhy] = useState("");

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !leave) return <div className="loading">Opening…</div>;
  if (isNew && !me.employee && !forOthers) {
    return (
      <article className="doc">
        <DocHeader back="/payroll/leave" backLabel="Leave" title="New leave request" />
        <p className="note" role="note">Your login is not linked to an employee yet, so there is no one to ask leave for. HR links it on your employee record.</p>
      </article>
    );
  }

  const value = draft.value;
  const mine = leave ? leave.employee === me.employee : true;
  const set = (key: keyof Leave & string, next: unknown) => draft.set(key, next as never);
  const ask = async () => {
    const outcome = await act.run("POST", ENDPOINT, {
      employee: value.employee, policy: value.policy, leave_type: value.leave_type,
      start_date: value.start_date, end_date: value.end_date || value.start_date,
      half_day: value.half_day, reason: value.reason,
    }, { done: "Leave asked for" });
    if (outcome.ok) {
      draft.reset();
      navigate(`/payroll/leave/${outcome.data.id}`, { replace: true });
    } else draft.failed(outcome.error);
  };
  const decide = (verb: "approve" | "reject") =>
    act.run("POST", `${ENDPOINT}${leave!.id}/${verb}/`, verb === "reject" ? { reason: why } : {},
      { done: verb === "approve" ? "Approved" : "Refused" });

  return (
    <article className="doc">
      <DocHeader back="/payroll/leave" backLabel="Leave" title="New leave request"
        number={leave ? `${leave.employee_name}, ${date(leave.start_date)}` : undefined}
        state={leave?.status} tone={leave ? TONES[leave.status] : undefined}>
        {isNew && can("hr.add_leaverequest") && (
          <ActionButton primary pending={act.pending} disabled={!value.start_date || !value.employee} onClick={() => void ask()}>
            Ask for it
          </ActionButton>
        )}
        {leave?.status === "pending" && !mine && can("hr.decide_leaverequest") && (
          <ActionButton primary pending={act.pending} onClick={() => void decide("approve")}>Approve</ActionButton>
        )}
        {(leave?.status === "pending" || leave?.status === "approved") && (mine || forOthers) && (
          <ActionButton pending={act.pending} onClick={() => void act.run("POST", `${ENDPOINT}${leave!.id}/cancel/`, {}, { done: "Cancelled: the days are back" })}>
            Cancel it
          </ActionButton>
        )}
      </DocHeader>
      <Sheet>
        <div className="field-grid">
          <Field label="Who">
            {(fid) => isNew && forOthers ? (
              <select id={fid} value={String(value.employee ?? "")} onChange={(e) => set("employee", e.target.value ? Number(e.target.value) : null)}>
                <option value="">Choose…</option>
                {(employees.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.employee_number} · {row.name}</option>)}
              </select>
            ) : <output id={fid}>{leave?.employee_name || me.employee_name || me.name}</output>}
          </Field>
          <Field label="Allowance" hint="What it draws on" errors={draft.errors.policy}>
            {(fid) => isNew ? (
              <select id={fid} value={String(value.policy ?? "")} onChange={(e) => {
                const chosen = (policies.data ?? []).find((row) => String(row.id) === e.target.value);
                set("policy", chosen ? chosen.id : null);
                if (chosen) set("leave_type", chosen.leave_type);
              }}>
                <option value="">None</option>
                {(policies.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
              </select>
            ) : <output id={fid}>{leave?.policy_name || "—"}</output>}
          </Field>
          <Field label="Kind" errors={draft.errors.leave_type}>
            {(fid) => isNew ? (
              <select id={fid} value={value.leave_type} onChange={(e) => set("leave_type", e.target.value)}>
                {TYPES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
            ) : <output id={fid}>{TYPES.find(([key]) => key === leave?.leave_type)?.[1] ?? leave?.leave_type}</output>}
          </Field>
          <Field label="From" errors={draft.errors.start_date}>
            {(fid) => isNew
              ? <input id={fid} type="date" value={value.start_date} onChange={(e) => set("start_date", e.target.value)} />
              : <output id={fid}>{date(leave?.start_date)}</output>}
          </Field>
          <Field label="To" hint="The last day away; empty for one day" errors={draft.errors.end_date}>
            {(fid) => isNew
              ? <input id={fid} type="date" value={value.end_date} min={value.start_date} onChange={(e) => set("end_date", e.target.value)} />
              : <output id={fid}>{date(leave?.end_date)}</output>}
          </Field>
          <Field label="Half a day" errors={draft.errors.half_day}>
            {(fid) => isNew
              ? <input id={fid} type="checkbox" checked={value.half_day} onChange={(e) => set("half_day", e.target.checked)} />
              : <output id={fid}>{leave?.half_day ? "Yes" : "No"}</output>}
          </Field>
          <Field label="Reason" wide errors={draft.errors.reason}>
            {(fid) => isNew
              ? <input id={fid} value={value.reason} onChange={(e) => set("reason", e.target.value)} />
              : <output id={fid}>{leave?.reason || "—"}</output>}
          </Field>
          {leave?.days_taken && (
            <Field label="Days it took">{(fid) => <output id={fid}>{quantity(leave.days_taken, 2)}</output>}</Field>
          )}
          {leave?.decided_by_name && (
            <Field label="Decided by">{(fid) => <output id={fid}>{leave.decided_by_name}</output>}</Field>
          )}
        </div>
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}
        {leave?.status === "pending" && !mine && can("hr.decide_leaverequest") && (
          <form className="refuse" onSubmit={(event) => { event.preventDefault(); void decide("reject"); }}>
            <label>Refuse, saying why <input aria-label="Why it is refused" value={why} onChange={(e) => setWhy(e.target.value)} /></label>
            <button type="submit" className="btn" disabled={!why.trim() || act.pending}>Refuse</button>
          </form>
        )}
      </Sheet>
    </article>
  );
}
