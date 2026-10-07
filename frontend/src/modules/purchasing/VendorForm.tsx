import { RelatedList } from "../../forms/Related";
import { date, money } from "../../lib/format";
import { PartyForm } from "../parties/PartyForm";
import { vendorButtons } from "./smart";

/** A vendor: who they are, what is on order from them and what we owe. */
export default function VendorForm() {
  return (
    <PartyForm role="vendor" base="/purchasing/vendors" plural="Vendors" smart={(party) => vendorButtons(party.id)} related={(party) => (
      <>
        <RelatedList title="Open orders" endpoint="/api/purchasing/purchase-orders/" permission="purchasing.view_purchaseorder"
          query={{ vendor: party.id, status: "confirmed" }} href={(row) => `/purchasing/orders/${row.id}`}
          cells={(row) => [String(row.number), date(String(row.order_date)), money(String(row.total))]} />
        <RelatedList title="Bills not yet paid" endpoint="/api/purchasing/bills/" permission="purchasing.view_bill"
          query={{ vendor: party.id, open: "true" }} href={(row) => `/purchasing/bills/${row.id}`}
          cells={(row) => [String(row.number), date(String(row.due_date)), money(String(row.amount_due))]} />
      </>
    )} />
  );
}
