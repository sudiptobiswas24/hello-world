import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { activitiesPanel, CAMPAIGN_REF, CUSTOMER_PICK, REP_PICK, STAGE_TONES } from "./crmRefs";

type Row = Record<string, unknown> & { id: number };
const open = (row: Row) => row.stage === "new" || row.stage === "qualified" || row.stage === "quoted";

/**
 * One piece of business with a customer: its stage, what it is worth and
 * the chance of it. Quoted from here into a draft quotation; won with the
 * order, or lost with the reason, after which it stands.
 */
export default function OpportunityForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/opportunities/"
      back="/sales/opportunities"
      backLabel="Opportunities"
      newTitle="New opportunity"
      heading={(row) => `${String(row.number ?? "")} · ${String(row.customer_name ?? "")}`}
      state={(row) => (row.stage ? { label: String(row.stage), tone: STAGE_TONES[String(row.stage)] ?? "draft" } : null)}
      permissions={{ add: "sales.add_opportunity", change: "sales.change_opportunity", delete: "sales.delete_opportunity" }}
      editable={open}
      fields={[
        { key: "customer", label: "Customer", kind: "pick", pick: CUSTOMER_PICK, createOnly: true, show: (row) => String(row.customer_name ?? "") },
        { key: "title", label: "The business", wide: true, hint: "20,000 cement sacks a month" },
        { key: "stage", label: "Stage", kind: "choice", choices: [["new", "New"], ["qualified", "Qualified"], ["quoted", "Quoted"]], initial: "new", hint: "Won and lost are said with the buttons" },
        { key: "value", label: "Worth, before tax", kind: "money" },
        { key: "probability", label: "Chance %", kind: "integer", hint: "Empty takes the stage's own: 10, 30, 60" },
        { key: "expected_on", label: "Expected", kind: "date" },
        { key: "campaign", label: "Campaign", kind: "ref", ref: CAMPAIGN_REF },
        { key: "owner", label: "Rep", kind: "pick", pick: REP_PICK, show: (row) => String(row.owner_name || "Nobody") },
        { key: "lead_number", label: "From lead", readOnly: true, existingOnly: true },
        { key: "quotation_number", label: "Quotation", readOnly: true, existingOnly: true },
        { key: "sales_order_number", label: "Order", readOnly: true, existingOnly: true },
        { key: "lost_reason", label: "Lost because", readOnly: true, existingOnly: true },
        { key: "closed_on", label: "Closed", kind: "date", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Quote it", path: "quote", permission: "sales.add_quotation", when: (row) => open(row) && !row.quotation, primary: true,
          done: "Draft quotation made",
          fields: [
            { key: "quotation_date", label: "Dated", kind: "date" },
            { key: "valid_until", label: "Valid until", kind: "date" },
          ],
          then: (result) => `/sales/quotations/${String(result.quotation)}` },
        { label: "Won", path: "win", permission: "sales.change_opportunity", when: open, done: "Won",
          fields: (row) => [
            { key: "sales_order", label: "The order", kind: "pick", hint: "Empty if it is not in yet",
              pick: { endpoint: "/api/sales/sales-orders/", permission: "sales.view_salesorder", query: { customer: String(row.customer) },
                label: (order: Record<string, unknown>) => `${String(order.number)} · ${String(order.order_date)}` } } as FieldDef,
          ] },
        { label: "Lost", path: "lose", permission: "sales.change_opportunity", when: open, danger: true, done: "Lost",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[activitiesPanel("opportunity", open)]}
    />
  );
}
