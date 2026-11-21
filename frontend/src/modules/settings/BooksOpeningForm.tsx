import { RecordScreen } from "../../views/RecordScreen";

const EQUITY = {
  endpoint: "/api/accounting/accounts/",
  label: (row: Record<string, unknown>) => `${String(row.code ?? "")} ${String(row.name ?? "")}`.trim(),
  query: { account_type: "equity", is_active: "true" },
  permission: "accounting.view_account",
};

/** The day the books opened, and the equity account opening balances stand against. Fixed once anything is posted on or before it. */
export default function BooksOpeningForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/books-opening/"
      back="/settings/books-opening"
      backLabel="Books opening"
      newTitle="The books' opening"
      heading={(row) => `Books opened on ${String(row.opened_on ?? "")}`}
      permissions={{ add: "accounting.add_booksopening", change: "accounting.change_booksopening" }}
      fields={[
        { key: "opened_on", label: "Books opened on", kind: "date",
          hint: "The day before the first day kept here: opening balances and assets typed in are dated it" },
        { key: "opening_account", label: "Opening balances against", kind: "ref", ref: EQUITY,
          hint: "An equity account, such as Opening Balance Equity" },
      ]}
    />
  );
}
