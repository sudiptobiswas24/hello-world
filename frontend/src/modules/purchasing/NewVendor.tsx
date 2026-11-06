import { NewParty } from "../parties/NewParty";

import { VENDOR_TERM_SECTIONS } from "./vendorTerms";

/** A new vendor in one form (NewParty): the bank they are paid into, their MSME and TDS standing, and what we buy on. */
export default function NewVendor() {
  return (
    <NewParty noun="vendor" plural="Vendors" base="/purchasing/vendors" endpoint="/api/purchasing/vendors/"
      termsPermission="purchasing.add_vendorprofile" sections={VENDOR_TERM_SECTIONS} paid />
  );
}
