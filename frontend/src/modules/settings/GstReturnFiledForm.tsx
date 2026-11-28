import { RecordScreen } from "../../views/RecordScreen";

/** One GST return as filed: which, for which month, the day and the portal's ARN. Recorded, never edited. */
export default function GstReturnFiledForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/gst-returns-filed/"
      back="/settings/gst-returns-filed"
      backLabel="GST returns filed"
      newTitle="Record a return filed"
      heading={(row) => `${String(row.return_name ?? "").toUpperCase()} ${String(row.period ?? "")}`}
      permissions={{ add: "accounting.add_gstreturnfiled" }}
      editable={() => false}
      fields={[
        { key: "return_name", label: "Return", kind: "choice", choices: [["gstr1", "GSTR-1"], ["gstr3b", "GSTR-3B"]], createOnly: true },
        { key: "period", label: "Month", hint: "YYYY-MM", createOnly: true },
        { key: "filed_on", label: "Filed on", kind: "date", createOnly: true },
        { key: "arn", label: "ARN", createOnly: true },
      ]}
    />
  );
}
