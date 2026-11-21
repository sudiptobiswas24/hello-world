import { NewParty } from "../parties/NewParty";

import { TERM_SECTIONS } from "./customerTerms";

/** A new customer in one form (NewParty): where their goods go, and the terms they trade on. */
export default function NewCustomer() {
  return (
    <NewParty noun="customer" plural="Customers" base="/sales/customers" endpoint="/api/sales/customers/"
      termsPermission="sales.add_customerprofile" sections={TERM_SECTIONS} shipping />
  );
}
