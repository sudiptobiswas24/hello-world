import { ReportView } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: number };

/** Deliveries out that no customer has yet signed for, oldest first: what a buyer paying from its own receipt has not counted. */
export default function Unacknowledged() {
  return (
    <ReportView<Row[], Row>
      title="Not yet signed for"
      endpoint="/api/sales/deliveries/unacknowledged/"
      params={[]}
      rows={(data) => data}
      columns={[
        { key: "date", label: "Shipped", kind: "date", width: "8rem" },
        { key: "number", label: "Delivery", width: "10rem" },
        { key: "customer", label: "Customer" },
        { key: "lr_number", label: "LR", width: "9rem" },
      ]}
      empty="Every delivery out has been signed for."
    />
  );
}
