import { RecordScreen } from "../../views/RecordScreen";
import { KEPT_ACCOUNT } from "../purchasing/refs";

/** A section tax is collected under, where it is owed until paid over, and what its base takes in. */
export default function TcsSectionForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/tcs-sections/"
      back="/settings/tcs-sections"
      backLabel="TCS sections"
      newTitle="New section"
      heading={(row) => `${String(row.code ?? "")} · ${String(row.name ?? "")}`}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "accounting.add_tcssection", change: "accounting.change_tcssection", delete: "accounting.delete_tcssection" }}
      fields={[
        { key: "code", label: "Code", hint: "206C(1), or 394 from April 2026" },
        { key: "name", label: "Collected on", hint: "Scrap" },
        { key: "form_code", label: "Return code", hint: "The code the quarterly return files it under" },
        { key: "base_includes_gst", label: "On the value with its GST", kind: "bool", initial: true },
        { key: "payable_account", label: "Payable account", kind: "pick", pick: KEPT_ACCOUNT, hint: "Where tax collected is owed until a challan pays it over" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
