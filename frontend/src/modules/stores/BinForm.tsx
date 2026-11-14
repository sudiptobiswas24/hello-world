import { RecordScreen } from "../../views/RecordScreen";
import { BIN, WAREHOUSE } from "./refs";

export default function BinForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/bins/"
      back="/stores/bins"
      backLabel="Bins"
      newTitle="New bin"
      heading={(row) => `${String(row.code)} · ${String(row.warehouse_name)}`}
      permissions={{ add: "inventory.add_storagebin", change: "inventory.change_storagebin", delete: "inventory.delete_storagebin" }}
      fields={[
        { key: "warehouse", label: "Warehouse", kind: "ref", ref: WAREHOUSE, createOnly: true },
        { key: "code", label: "Bin" },
        { key: "name", label: "Name" },
        { key: "parent", label: "Inside", kind: "ref", ref: BIN, hint: "A rack's shelf is inside the rack" },
        { key: "sequence", label: "Picking order", kind: "integer", initial: "0" },
        { key: "is_pickable", label: "Picked from", kind: "bool", initial: true },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
