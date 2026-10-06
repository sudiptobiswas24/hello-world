import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const DELIVERY = {
  endpoint: "/api/sales/deliveries/", permission: "sales.view_delivery", query: { posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.customer_name ?? row.customer_code ?? "")}`,
};

/**
 * A shipment's test certificate: the batches it carried and what each
 * was found to be, frozen when issued. Withdrawn with a reason, never
 * edited; the printed page is what the customer is sent.
 */
export default function CertificateForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/test-certificates/"
      back="/quality/certificates"
      backLabel="Test certificates"
      newTitle="Issue a test certificate"
      heading={(row) => `${String(row.number)} · ${String(row.customer_name)}`}
      state={(row) => (row.voided_at ? { label: "Withdrawn", tone: "draft" } : { label: "Issued", tone: "done" })}
      permissions={{ add: "manufacturing.add_testcertificate" }}
      fields={[
        { key: "delivery", label: "For delivery", kind: "pick", pick: DELIVERY, createOnly: true,
          show: (row) => String(row.delivery_number) },
        { key: "issued_on", label: "Issued", kind: "date", readOnly: true },
        { key: "voided_reason", label: "Withdrawn because", readOnly: true },
      ]}
      note={(row) => (row && !row.voided_at
        ? <a className="btn" href={`/api/manufacturing/test-certificates/${String(row.id)}/print/`} target="_blank" rel="noreferrer">Print</a>
        : null)}
      actions={[
        { label: "Withdraw", path: "void", permission: "manufacturing.change_testcertificate", danger: true,
          when: (row) => !row.voided_at, done: "Withdrawn", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
    />
  );
}
