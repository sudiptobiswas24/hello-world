import { date, money } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { bill: number };
type Figures = Record<string, string | null>;
type Data = {
  rows: Row[];
  totals: Figures;
  interest_state: string;
  bank_rates: { applies_from: string; rate_percent: string; interest_percent: string; confirmed: boolean }[];
  note?: Figures;
};

const PARAMS: ParamDef[] = [
  { key: "start", label: "Bills from", kind: "date", required: true, initial: "-90" },
  { key: "end", label: "To", kind: "date", required: true, initial: "today" },
  { key: "as_of", label: "As of", kind: "date", required: true, initial: "today" },
  { key: "year_start", label: "Year's note from", kind: "date" },
  { key: "year_end", label: "to", kind: "date" },
];

/** An interest figure, or the words for one not worked: never a blank that reads as nothing owed. */
const interest = (value: unknown) => (value === null || value === undefined ? "Not worked" : money(String(value)));

/**
 * Micro and small vendors' bills, a row per delivery's part, against the
 * MSME Act's days: the agreed days, never more than 45 from acceptance, or
 * 15 with none agreed. What stands unpaid past its date at the year's end
 * is added back to profit (43B(h)). Paid late, the Act adds compound
 * interest at three times the bank rate (s.16): shown here, never posted.
 */
export default function MsmeReport() {
  return (
    <ReportView<Data, Row>
      title="MSME payments"
      endpoint="/api/purchasing/purchasing-reports/msme-statement/"
      params={PARAMS}
      rows={(data) => data.rows.map((row, index) => ({ ...row, id: index + 1 }))}
      above={(data) => (
        <>
          <p className={data.interest_state ? "form-error" : "note"} role="note">
            {data.interest_state
              ? `Interest: ${data.interest_state}`
              : "Interest is compound with monthly rests at three times the bank rate, from the day after each part was due (MSMED Act s.16). It is shown for the accounts and is not posted."}
            {data.bank_rates.length > 0 && ` Bank rate: ${data.bank_rates.map((rate) =>
              `${rate.rate_percent}% from ${date(rate.applies_from)}${rate.confirmed ? "" : " (not confirmed)"}`).join("; ")}.`}
          </p>
          {data.note && (
            <table className="data" aria-label="For the year's accounts (MSMED Act s.22)">
              <caption>For the year's accounts, {date(String(data.note.year_start))} to {date(String(data.note.year_end))} (MSMED Act s.22)</caption>
              <tbody>
                <tr><th scope="row">(i) Principal unpaid at the year's end</th><td>{money(String(data.note.principal_unpaid))}</td></tr>
                <tr><th scope="row">of it past its day (added back, 43B(h))</th><td>{money(String(data.note.principal_unpaid_past_its_day))}</td></tr>
                <tr><th scope="row">(i) Interest due on it</th><td>{interest(data.note.interest_on_unpaid)}</td></tr>
                <tr><th scope="row">(ii) Paid in the year after its day</th><td>{money(String(data.note.paid_after_the_day))}</td></tr>
                <tr><th scope="row">(ii) Interest paid with it</th><td>Not recorded here</td></tr>
                <tr><th scope="row">(iii) Interest for the delay on what was paid late</th><td>{interest(data.note.interest_on_paid_late)}</td></tr>
                <tr><th scope="row">(iv) Interest accrued and unpaid at the year's end</th><td>{interest(data.note.interest_accrued_unpaid)}</td></tr>
                <tr><th scope="row">Interest of the year, not deductible (s.23)</th><td>{interest(data.note.interest_of_the_year)}</td></tr>
              </tbody>
            </table>
          )}
        </>
      )}
      columns={[
        { key: "number", label: "Bill", width: "9rem" },
        { key: "vendor", label: "Vendor" },
        { key: "category", label: "", width: "5rem" },
        { key: "bill_date", label: "Dated", kind: "date", width: "8rem" },
        { key: "accepted_on", label: "Accepted", width: "8rem", render: (row) => (row.accepted_on ? date(String(row.accepted_on)) : "Objection stands") },
        { key: "amount", label: "Delivery's part", kind: "money", width: "9rem" },
        { key: "due", label: "Due by the Act", width: "9rem", render: (row) => (row.due_pending ? "Objection stands" : date(String(row.due))) },
        { key: "paid_on", label: "Paid", width: "8rem", render: (row) => (row.paid_on ? date(String(row.paid_on)) : "Not yet") },
        { key: "days_late", label: "Days late", width: "6rem", render: (row) => (row.due_pending ? "Pending" : String(row.days_late)) },
        { key: "unpaid", label: "Unpaid", kind: "money", width: "9rem" },
        { key: "at_risk", label: "At risk", kind: "money", width: "9rem" },
        { key: "interest", label: "Interest (s.16)", kind: "money", width: "9rem", render: (row) => interest(row.interest) },
      ]}
      foot={(_rows, data) => ["Total", null, null, null, null, money(String(data.totals.amount)), null, null, null,
        money(String(data.totals.unpaid)), money(String(data.totals.at_risk)), interest(data.totals.interest)]}
      empty="No micro or small vendor billed in these dates."
    />
  );
}
