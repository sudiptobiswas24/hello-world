import { useParams } from "react-router";

import AccountForm from "./AccountForm";
import LedgerPage from "./Ledger";

/**
 * An account's page is its ledger. A new one has no ledger yet: it is
 * made at chart/new, in the form it is later changed in (chart/:id/edit).
 */
export default function Account() {
  const { id } = useParams();
  return id === "new" ? <AccountForm /> : <LedgerPage />;
}
