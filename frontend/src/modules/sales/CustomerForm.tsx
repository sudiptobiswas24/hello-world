import { useParams } from "react-router";

import { RelatedList } from "../../forms/Related";
import { date, money } from "../../lib/format";
import { PartyForm } from "../parties/PartyForm";
import NewCustomer from "./NewCustomer";
import { SalesTerms } from "./SalesTerms";
import { customerButtons } from "./smart";

/** A customer: who they are, their terms, their open orders and what they owe. Made in one form. */
export default function CustomerForm() {
  const { id } = useParams();
  if (id === "new") return <NewCustomer />;
  return (
    <PartyForm role="customer" base="/sales/customers" plural="Customers" smart={(party) => customerButtons(party.id)} related={(party) => (
      <>
        <SalesTerms party={party.id} />
        <RelatedList title="Open orders" endpoint="/api/sales/sales-orders/" permission="sales.view_salesorder"
          query={{ customer: party.id, status: "confirmed" }} href={(row) => `/sales/orders/${row.id}`}
          cells={(row) => [String(row.number), date(String(row.order_date)), money(String(row.total))]} />
        <RelatedList title="Invoices not yet paid" endpoint="/api/sales/invoices/" permission="sales.view_invoice"
          query={{ customer: party.id, open: "true" }} href={(row) => `/sales/invoices/${row.id}`}
          cells={(row) => [String(row.number), date(String(row.due_date)), money(String(row.amount_due))]} />
      </>
    )} />
  );
}
