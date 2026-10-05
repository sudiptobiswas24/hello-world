import { PartyList } from "../parties/PartyList";

export default function Customers() {
  return <PartyList role="customer" title="Customers" base="/sales/customers" />;
}
