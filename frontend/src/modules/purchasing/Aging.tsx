import { AgingReport } from "../money/Aging";

/** What is owed to vendors, by how late it is. */
export default function Aging() {
  return <AgingReport title="Payables by age" endpoint="/api/purchasing/purchasing-reports/aging/" documents="bills"
    document="Bill" party="vendor" href={(id) => `/purchasing/bills/${id}`} />;
}
