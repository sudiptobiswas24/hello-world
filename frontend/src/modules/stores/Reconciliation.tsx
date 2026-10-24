import { money } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { account: string; stock_value: string; ledger_balance: string; difference: string; items: number; [key: string]: unknown }
interface Data { balanced: boolean; total_stock_value: string; total_ledger_balance: string; difference: string; rows: Row[]; unvalued_items: string[] }

const PARAMS: ParamDef[] = [{ key: "as_of", label: "As at", kind: "date", initial: "today" }];

/** Whether the shelves and the books agree, account by account: they must, to the paisa. */
export default function Reconciliation() {
  return (
    <ReportView<Data, Row>
      title="Stock against the books"
      endpoint="/api/inventory/stock-reports/reconciliation/"
      params={PARAMS}
      rows={(data) => data.rows}
      above={(data) => (
        <p className={data.balanced ? "note" : "form-error"} role="note">
          {data.balanced ? "The stock agrees with the books." : `They differ by ${money(data.difference)}.`}
          {data.unvalued_items.length > 0 && ` Items with no value: ${data.unvalued_items.join(", ")}.`}
        </p>
      )}
      columns={[
        { key: "account", label: "Account" },
        { key: "items", label: "Items", kind: "quantity", render: (row) => String(row.items) },
        { key: "stock_value", label: "On the shelves", kind: "money" },
        { key: "ledger_balance", label: "In the books", kind: "money" },
        { key: "difference", label: "Difference", kind: "money" },
      ]}
      foot={(_rows, data) => ["Total", null, money(data.total_stock_value), money(data.total_ledger_balance), money(data.difference)]}
    />
  );
}
