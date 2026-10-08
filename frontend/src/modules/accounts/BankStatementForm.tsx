import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { MONEY_ACCOUNT } from "./refs";

type Row = Record<string, unknown>;
type Line = Row & { id: number };

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const lineLabel = (row: Row) => `${String(row.date)} · ${String(row.description || row.reference || "")} · ${String(row.amount)}`;
/** The statement's lines nothing explains yet: a match or a posting is for one of these. */
const unexplained = (statement: Row): FieldDef["ref"] => ({
  endpoint: "/api/accounting/bank-statement-lines/", permission: "accounting.view_bankstatementline", label: lineLabel,
  query: { statement: String(statement.id), payment__isnull: "true", journal_entry__isnull: "true", returned_payment__isnull: "true" },
});

const FIGURES: [string, string][] = [
  ["ledger_balance", "The books at the statement's end"], ["unpresented_total", "Payments the bank has not seen"],
  ["statement_balance", "What the bank says"], ["statement_difference", "The statement's own lines, off by"],
  ["difference", "Unexplained"],
];
const resolution = (row: Row) => (row.payment_number ? `Payment ${String(row.payment_number)}`
  : row.returned_payment_number ? `Returned: payment ${String(row.returned_payment_number)}`
  : row.journal_entry ? `Posted, JE-${String(row.journal_entry)}` : "");

/**
 * A month of a bank account as the bank reports it, matched line by line
 * to the books. The bookkeeper keys it in and matches; the controller
 * posts what the bank originated and signs it off.
 */
export default function BankStatementForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/bank-statements/"
      back="/accounts/bank-statements"
      backLabel="Bank statements"
      newTitle="New bank statement"
      heading={(row) => `${String(row.bank_account_name ?? "")} to ${String(row.end_date ?? "")}`}
      state={(row) => (row.closed ? { label: "Closed", tone: "done" } : { label: "Open", tone: "draft" })}
      permissions={{ add: "accounting.add_bankstatement", change: "accounting.change_bankstatement", delete: "accounting.delete_bankstatement" }}
      editable={(row) => !row.closed}
      fields={[
        { key: "bank_account", label: "Bank account", kind: "pick", pick: MONEY_ACCOUNT, createOnly: true },
        { key: "reference", label: "Reference", hint: "The statement's own number or period" },
        { key: "start_date", label: "From", kind: "date" },
        { key: "end_date", label: "To", kind: "date" },
        { key: "opening_balance", label: "Opening balance", kind: "money", negative: true },
        { key: "closing_balance", label: "Closing balance", kind: "money", negative: true },
      ]}
      links={[{ label: "Import the bank's file", same: true, href: (row) => `/accounts/bank-import?statement=${String(row.id)}`, when: (row) => !row.closed }]}
      actions={[
        { label: "Match the obvious", path: "auto_match", permission: "accounting.change_bankstatementline", when: (row) => !row.closed,
          done: "Matched what had one payment of the same amount nearby" },
        { label: "Match a line", path: "match", permission: "accounting.change_bankstatementline", when: (row) => !row.closed,
          done: "Matched",
          fields: (statement) => [
            { key: "line", label: "Line", kind: "ref", ref: unexplained(statement) },
            { key: "payment", label: "Payment", kind: "pick", pick: {
              endpoint: "/api/accounting/payments/", permission: "accounting.view_payment",
              query: { bank_account: String(statement.bank_account), posted: "true" },
              label: (row: Row) => `${String(row.number)} · ${String(row.party_name)} · ${String(row.amount)}` } },
          ] },
        { label: "Post a line", path: "post_line", permission: "accounting.post_journalentry", when: (row) => !row.closed,
          done: "Posted",
          fields: (statement) => [
            { key: "line", label: "Line", kind: "ref", ref: unexplained(statement) },
            { key: "account", label: "To account", kind: "pick", pick: ACCOUNT, hint: "Bank charges, interest received" },
            { key: "party", label: "Party", kind: "pick", pick: PARTY, hint: "Only if it was someone's" },
            { key: "memo", label: "Memo" },
          ] },
        { label: "Close", path: "close", permission: "accounting.close_bankstatement", when: (row) => !row.closed, primary: true,
          done: "Closed: reconciled" },
        { label: "Reopen", path: "reopen", permission: "accounting.close_bankstatement", when: (row) => Boolean(row.closed),
          done: "Reopened" },
      ]}
      panels={[
        {
          title: "Books to bank", permission: "accounting.view_bankstatement", endpoint: "", query: () => ({}),
          read: {
            path: (statement) => `/api/accounting/bank-statements/${String(statement.id)}/reconciliation/`,
            rows: (data) => FIGURES.map(([key, label], index): Line => ({ id: index, label, amount: (data as Row)[key] })),
          },
          columns: [{ key: "label", label: "" }, { key: "amount", label: "Amount", kind: "money", width: "12rem" }],
        },
        {
          title: "Lines", permission: "accounting.view_bankstatementline", endpoint: "/api/accounting/bank-statement-lines/",
          query: (statement) => ({ statement: String(statement.id) }),
          columns: [
            { key: "date", label: "Date", kind: "date", width: "8rem" },
            { key: "description", label: "Description" },
            { key: "reference", label: "Reference", width: "9rem" },
            { key: "amount", label: "Amount", kind: "money", width: "10rem" },
            { key: "payment_number", label: "Explained by", width: "12rem", render: resolution },
          ],
          adder: {
            label: "Add a line", permission: "accounting.add_bankstatementline", when: (statement) => !statement.closed,
            url: () => "/api/accounting/bank-statement-lines/",
            fields: [
              { key: "date", label: "Date", kind: "date" },
              { key: "description", label: "Description" },
              { key: "reference", label: "Reference" },
              { key: "amount", label: "Amount", kind: "money", negative: true, hint: "Money in is positive, money out negative, as the bank shows it" },
            ],
            body: (values, statement) => ({ ...values, statement: statement.id }),
          },
          remover: { permission: "accounting.delete_bankstatementline", url: (row) => `/api/accounting/bank-statement-lines/${String(row.id)}/`,
            when: (statement) => !statement.closed },
          rowActions: [
            { label: "Unmatch", permission: "accounting.change_bankstatementline", method: "POST", done: "Unmatched",
              when: (row) => Boolean(row.payment || row.returned_payment), url: (row) => `/api/accounting/bank-statement-lines/${String(row.id)}/unmatch/` },
            { label: "Reverse", permission: "accounting.post_journalentry", method: "POST", done: "Posting reversed",
              confirm: "Reverse this line's posting? The entry stays, reversed on its own date.",
              when: (row) => Boolean(row.journal_entry), url: (row) => `/api/accounting/bank-statement-lines/${String(row.id)}/reverse_posting/` },
          ],
        },
      ]}
    />
  );
}
