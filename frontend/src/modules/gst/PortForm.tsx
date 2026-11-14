import { RecordScreen } from "../../views/RecordScreen";

/**
 * A port, ICD or CFS in India: where an export's goods go from the plant,
 * and what the e-way bill to it is made out to. An invoice keeps the
 * address as it read when it posted, so changing it here changes no
 * invoice already posted.
 */
export default function PortForm() {
  return (
    <RecordScreen
      endpoint="/api/core/addresses/"
      back="/accounts/ports"
      backLabel="Ports and depots"
      newTitle="New port or depot"
      heading={(row) => String(row.label || row.line1 || "Port or depot")}
      permissions={{ add: "core.add_address", change: "core.change_address" }}
      fields={[
        { key: "label", label: "Name", hint: "JNPT, ICD Dighi, a CFS" },
        { key: "address_type", label: "Kind", kind: "choice", choices: [["other", "Port or depot"]], initial: "other", createOnly: true },
        { key: "line1", label: "Address", wide: true },
        { key: "line2", label: "Second line", wide: true },
        { key: "city", label: "Town" },
        { key: "state", label: "State", hint: "As the GST network spells it, or its two-digit code" },
        { key: "postal_code", label: "PIN" },
        { key: "is_active", label: "In use", kind: "bool", initial: true },
      ]}
    />
  );
}
