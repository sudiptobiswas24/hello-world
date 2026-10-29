import { RecordScreen } from "../../views/RecordScreen";
import { REP, TEAM } from "./extraRefs";

/** One target: a team's or a rep's, net of tax, between two dates. */
export default function TargetForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/sales-targets/"
      back="/sales/targets"
      backLabel="Sales targets"
      newTitle="New sales target"
      heading={(row) => String(row.who ?? "")}
      permissions={{ add: "sales.add_salestarget", change: "sales.change_salestarget", delete: "sales.delete_salestarget" }}
      fields={[
        { key: "team", label: "Team", kind: "ref", ref: TEAM, hint: "Either a team or a rep" },
        { key: "rep", label: "Rep", kind: "ref", ref: REP },
        { key: "period_start", label: "From", kind: "date" },
        { key: "period_end", label: "To", kind: "date" },
        { key: "amount", label: "Target, net of tax", kind: "money" },
        { key: "note", label: "Note", wide: true },
      ]}
    />
  );
}
