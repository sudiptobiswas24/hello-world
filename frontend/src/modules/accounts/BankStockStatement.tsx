import { money } from "../../lib/format";
import { ReportView } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: number };
interface Statement {
  stock: { stock_class: string; label: string; value: string }[];
  stock_total: string; work_in_progress: string; creditors: string; paid_stock: string;
  stock_margin_percent: string; paid_stock_after_margin: string;
  debtors_within_90: string; debtors_beyond_90: string; debtors_total: string;
  debtor_margin_percent: string; debtors_after_margin: string; drawing_power: string;
}

const line = (id: string, label: string, amount: string, strong = false) =>
  ({ id, label, amount, strong }) as unknown as Row;

/**
 * The month's statement for the bank: stock by class at cost, work in
 * progress, creditors, debtors within ninety days, and the drawing power
 * the bank's margins leave. Read, never stored; print it for the bank.
 */
export default function BankStockStatement() {
  return (
    <ReportView<Statement, Row>
      title="Bank stock statement"
      endpoint="/api/web/bank-stock-statement/"
      params={[
        { key: "as_of", label: "As at", kind: "date", initial: "today", required: true },
        { key: "stock_margin", label: "Margin on stock", kind: "choice", initial: "25", required: true,
          choices: [["20", "20%"], ["25", "25%"], ["30", "30%"], ["40", "40%"], ["50", "50%"]] },
        { key: "debtor_margin", label: "Margin on debtors", kind: "choice", initial: "40", required: true,
          choices: [["25", "25%"], ["40", "40%"], ["50", "50%"]] },
      ]}
      rows={(data) => [
        ...data.stock.map((row) => line(row.stock_class || "unclassified", row.label, row.value)),
        line("wip", "Work in progress", data.work_in_progress),
        line("stock_total", "Stock and work in progress", data.stock_total, true),
        line("creditors", "Less creditors", data.creditors),
        line("paid_stock", "Paid stock", data.paid_stock),
        line("stock_after", `Paid stock after ${data.stock_margin_percent}% margin`, data.paid_stock_after_margin, true),
        line("debtors_within", "Debtors within 90 days", data.debtors_within_90),
        line("debtors_beyond", "Debtors beyond 90 days, not counted", data.debtors_beyond_90),
        line("debtors_after", `Debtors after ${data.debtor_margin_percent}% margin`, data.debtors_after_margin, true),
        line("drawing_power", "Drawing power", data.drawing_power, true),
      ]}
      columns={[
        { key: "label", label: "", render: (row) => (row.strong ? <strong>{String(row.label)}</strong> : String(row.label)) },
        { key: "amount", label: "Amount", kind: "money", width: "14rem",
          render: (row) => (row.strong ? <strong>{money(row.amount as string)}</strong> : money(row.amount as string)) },
      ]}
      empty="Nothing to state."
    />
  );
}
