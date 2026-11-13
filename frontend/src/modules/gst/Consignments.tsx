import { Link, useNavigate } from "react-router";

import { useAct, useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { date, money } from "../../lib/format";

interface Consignment {
  delivery: number;
  number: string;
  delivery_date: string;
  vehicle_number: string;
  lr_number: string;
  transporter_name: string;
  document_type?: string;
  document_number?: string;
  value?: string;
  refused?: string;
  eway_bill: number | null;
  eway_bill_number: string;
}

/**
 * The trucks a posted invoice went on, each with its share of the invoice
 * and its own e-way bill: the portal wants one for each consignment. Asked
 * only of those who may read e-way bills.
 */
export function Consignments({ invoice }: { invoice: number }) {
  const { can } = useAccess();
  const navigate = useNavigate();
  const act = useAct<{ id: number }>();
  const found = useGet<Consignment[]>("/api/gst/eway-bills/consignments/", { invoice });
  if (!found.data || found.data.length === 0) return null;
  const make = (row: Consignment) => void act.run("POST", "/api/gst/eway-bills/", { invoice, carried_on: row.delivery },
    { done: "E-way bill drafted", onDone: ((bill: { id: number }) => navigate(`/accounts/eway-bills/${bill.id}`)) as never });
  return (
    <section className="related-list" aria-label="Trucks">
      <h2>Trucks it went on</h2>
      <table className="data" aria-label="Trucks it went on">
        <thead><tr><th>Delivery</th><th>Shipped</th><th>Vehicle</th><th>LR</th><th>Moves on</th><th className="num">Value</th><th>E-way bill</th></tr></thead>
        <tbody>
          {found.data.map((row) => (
            <tr key={row.delivery}>
              <td><Link to={`/sales/deliveries/${row.delivery}`}>{row.number}</Link></td>
              <td>{date(row.delivery_date)}</td>
              <td>{row.vehicle_number || "—"}</td>
              <td>{row.lr_number || "—"}</td>
              <td>{row.refused ? row.refused : `${row.document_type === "CHL" ? "Challan" : "Invoice"} ${row.document_number}`}</td>
              <td className="num">{row.value ? money(row.value) : "—"}</td>
              <td>
                {row.eway_bill
                  ? <Link to={`/accounts/eway-bills/${row.eway_bill}`}>{row.eway_bill_number || "To generate"}</Link>
                  : can("gst.add_ewaybill") && !row.refused
                    ? <ActionButton pending={act.pending} onClick={() => make(row)}>Make e-way bill for {row.number}</ActionButton>
                    : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
