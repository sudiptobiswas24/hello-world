import { money } from "../../lib/format";
import { RecordScreen } from "../../views/RecordScreen";
import { CURRENCY, ITEM, UOM, VENDOR, status } from "./refs";

type Row = Record<string, unknown> & { id: number };
type Quote = { id: number; line: number; unit_price: string; lead_time_days: number | null };
type Invited = Row & { vendor_name: string; declined: boolean; awarded: boolean; quotes: Quote[] };

const is = (...states: string[]) => (row: Row) => states.includes(String(row.status));
const lines = (record: Row) => (record.lines as Row[]) ?? [];
const invited = (record: Row) => (record.invited as Invited[]) ?? [];

/**
 * The same requirement put to several vendors and their answers compared
 * before anyone commits. Its lines are fixed when it goes out; quotes come
 * in while it is out; the award orders from one vendor at the prices they
 * quoted, and is the record of how they were chosen.
 */
export default function RfqForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/rfqs/"
      back="/purchasing/rfqs"
      backLabel="Requests for quotation"
      newTitle="New request for quotation"
      heading={(row) => String(row.number || "Request for quotation")}
      state={status}
      permissions={{ add: "purchasing.add_requestforquotation", change: "purchasing.change_requestforquotation", delete: "purchasing.delete_requestforquotation" }}
      editable={is("draft")}
      fields={[
        { key: "description", label: "For", kind: "textarea", wide: true },
        { key: "issue_date", label: "Out on", kind: "date" },
        { key: "response_due", label: "Answers by", kind: "date" },
        { key: "currency", label: "Currency", kind: "ref", ref: CURRENCY },
      ]}
      actions={[
        { label: "Send it out", path: "issue", permission: "purchasing.change_requestforquotation", when: is("draft"), primary: true, done: "Sent out" },
        { label: "Cancel", path: "cancel", permission: "purchasing.change_requestforquotation", when: is("draft", "sent"), danger: true, done: "Cancelled" },
      ]}
      panels={[{
        title: "What is asked for", permission: "purchasing.view_rfqline", endpoint: "", query: () => ({}),
        rows: (record) => lines(record) as Row[],
        columns: [
          { key: "item_label", label: "Item" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
          { key: "notes", label: "Notes" },
        ],
        adder: { label: "Add a line", permission: "purchasing.add_rfqline", when: is("draft"),
          url: () => "/api/purchasing/rfq-lines/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "uom", label: "Unit", kind: "ref", ref: UOM },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "notes", label: "Notes", kind: "text" },
          ],
          body: (values, record) => ({ ...values, rfq: record.id }) },
        remover: { permission: "purchasing.delete_rfqline", when: is("draft"), url: (row) => `/api/purchasing/rfq-lines/${row.id}/` },
      }, {
        title: "Vendors asked", permission: "purchasing.view_rfqinvitation", endpoint: "", query: () => ({}),
        // Whether the request is still out travels with each row: a row's
        // action is asked of the row, and award and decline need both.
        rows: (record) => invited(record).map((row) => ({
          ...row, out: record.status === "sent", quoted_all: row.quotes.length === lines(record).length && lines(record).length > 0,
          quoted: `${row.quotes.length} of ${lines(record).length} lines`,
        })),
        columns: [
          { key: "vendor_name", label: "Vendor" },
          { key: "quoted", label: "Quoted", width: "9rem" },
          { key: "declined", label: "", width: "8rem", render: (row) => (row.awarded ? "Awarded" : row.declined ? "Declined" : "") },
        ],
        adder: { label: "Ask a vendor", permission: "purchasing.add_rfqinvitation", when: is("draft", "sent"),
          url: () => "/api/purchasing/rfq-invitations/",
          fields: [{ key: "vendor", label: "Vendor", kind: "pick", pick: VENDOR }],
          body: (values, record) => ({ ...values, rfq: record.id }) },
        remover: { permission: "purchasing.delete_rfqinvitation", when: is("draft"), url: (row) => `/api/purchasing/rfq-invitations/${row.id}/` },
        rowActions: [
          { label: "Award", permission: "purchasing.add_purchaseorder", when: (row) => Boolean(row.out && row.quoted_all && !row.declined),
            url: (_row, record) => `/api/purchasing/rfqs/${record.id}/award/`, body: (row) => ({ invitation: row.id }),
            confirm: "Order the whole requirement from this vendor at the prices they quoted?", done: "Awarded: the order is raised" },
          { label: "Declined", permission: "purchasing.change_rfqinvitation",
            when: (row) => Boolean(row.out && !row.declined && (row.quotes as Quote[]).length === 0),
            url: (row) => `/api/purchasing/rfq-invitations/${row.id}/decline/`, done: "Recorded as declined" },
        ],
      }, {
        title: "Quotes", permission: "purchasing.view_rfqinvitation", endpoint: "", query: () => ({}),
        rows: (record) => invited(record).flatMap((row) => row.quotes.map((quote) => ({
          id: quote.id, vendor: row.vendor_name, unit_price: quote.unit_price, lead_time_days: quote.lead_time_days,
          item: String(lines(record).find((line) => line.id === quote.line)?.item_label ?? ""),
        }))),
        columns: [
          { key: "vendor", label: "Vendor" },
          { key: "item", label: "Item" },
          { key: "unit_price", label: "Each", kind: "money", width: "9rem" },
          { key: "lead_time_days", label: "Days to deliver", width: "9rem" },
        ],
        adder: { label: "Enter a quote", permission: "purchasing.change_rfqinvitation", when: is("sent"),
          url: (record) => `/api/purchasing/rfqs/${record.id}/quote/`,
          fields: (record) => [
            { key: "invitation", label: "Vendor", kind: "choice",
              choices: invited(record).filter((row) => !row.declined).map((row) => [String(row.id), row.vendor_name]) },
            { key: "line", label: "Item", kind: "choice", choices: lines(record).map((line) => [String(line.id), String(line.item_label)]) },
            { key: "unit_price", label: "Each", kind: "money" },
            { key: "lead_time_days", label: "Days to deliver", kind: "integer" },
          ],
          body: (values) => values },
      }, {
        title: "Side by side", permission: "purchasing.view_requestforquotation", endpoint: "", query: () => ({}),
        read: {
          path: (record) => `/api/purchasing/rfqs/${record.id}/comparison/`,
          rows: (data) => ((data as { totals: Row[] }).totals ?? []).map((row) => ({ ...row, id: Number(row.invitation) })),
        },
        columns: [
          { key: "vendor", label: "Vendor" },
          { key: "total", label: "The whole requirement", render: (row) => (row.total == null ? "Did not quote every line" : money(String(row.total))) },
        ],
      }]}
    />
  );
}
