import { ReportView } from "../../views/ReportView";

type Head = { deposited: string; set_off: string; balance: string };
type Ledger = { account: number; heads: Record<string, Head>; balance: string; ledger: string; difference: string };
type Row = Record<string, unknown> & { id: string };

/** The electronic cash ledger by head, as the challans deposited and set off, against its account. */
export default function GstCashLedger() {
  return (
    <ReportView<Ledger[], Row>
      title="GST cash ledger"
      endpoint="/api/gst/cash-payments/cash_ledger/"
      params={[]}
      rows={(data) => data.flatMap((ledger) => Object.entries(ledger.heads).map(([head, money]) => ({
        id: `${ledger.account}-${head}`, account: ledger.account, head: head.toUpperCase(), ...money,
        ledger: ledger.ledger, difference: ledger.difference,
      })))}
      columns={[
        { key: "head", label: "Head", width: "6rem" },
        { key: "deposited", label: "Deposited", kind: "money", width: "10rem" },
        { key: "set_off", label: "Set off", kind: "money", width: "10rem" },
        { key: "balance", label: "Balance", kind: "money", width: "10rem" },
        { key: "ledger", label: "Account balance", kind: "money", width: "10rem" },
        { key: "difference", label: "Difference", kind: "money", width: "10rem" },
      ]}
      empty="No challan has been paid yet."
    />
  );
}
