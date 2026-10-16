import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { delivery: number };

// Every transporter at once: the list is short, and each row names who carried it.
const PARAMS: ParamDef[] = [];

/** Deliveries a transporter carried that no freight bill names yet: what its next bill should charge for, and no more. */
export default function UnbilledFreight() {
  return (
    <ReportView<Row[], Row>
      title="Freight not yet billed"
      endpoint="/api/purchasing/purchasing-reports/unbilled-freight/"
      params={PARAMS}
      rows={(data) => data.map((row) => ({ ...row, id: row.delivery }))}
      columns={[
        { key: "date", label: "Shipped", kind: "date", width: "8rem" },
        { key: "number", label: "Delivery", width: "10rem" },
        { key: "transporter", label: "Transporter" },
        { key: "customer", label: "To" },
        { key: "lr_number", label: "LR", width: "9rem" },
        { key: "vehicle_number", label: "Vehicle", width: "9rem" },
      ]}
      empty="Every delivery carried has been billed for."
    />
  );
}
