import { useParams } from "react-router";

import { RelatedList } from "../../forms/Related";
import { date, money } from "../../lib/format";
import { PartyForm } from "../parties/PartyForm";
import NewVendor from "./NewVendor";
import { PurchaseTerms } from "./PurchaseTerms";
import { vendorButtons } from "./smart";

/** A vendor: who they are, what we buy on, what is on order from them and what we owe. Made in one form. */
export default function VendorForm() {
  const { id } = useParams();
  if (id === "new") return <NewVendor />;
  return (
    <PartyForm role="vendor" base="/purchasing/vendors" plural="Vendors" smart={(party) => vendorButtons(party.id)} related={(party) => (
      <>
        <PurchaseTerms party={party.id} />
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
