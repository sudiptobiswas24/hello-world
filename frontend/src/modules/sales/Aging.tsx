import { AgingReport } from "../money/Aging";

/** What customers owe, by how late it is. */
export default function Aging() {
  return <AgingReport title="Receivables by age" endpoint="/api/sales/invoices/aging/" documents="invoices"
    document="Invoice" party="customer" href={(id) => `/sales/invoices/${id}`} />;
}
