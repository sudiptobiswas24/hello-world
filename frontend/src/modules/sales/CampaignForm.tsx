import { money } from "../../lib/format";
import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };

/** One campaign and what it brought: leads, customers made of them, business opened and won. */
export default function CampaignForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/campaigns/"
      back="/sales/campaigns"
      backLabel="Campaigns"
      newTitle="New campaign"
      heading={(row) => `${String(row.code ?? "")} · ${String(row.name ?? "")}`}
      permissions={{ add: "sales.add_campaign", change: "sales.change_campaign", delete: "sales.delete_campaign" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "channel", label: "Channel", kind: "choice", initial: "other",
          choices: [["exhibition", "Exhibition"], ["print", "Print"], ["digital", "Digital"], ["field", "Field visits"], ["referral", "Referral drive"], ["other", "Other"]] },
        { key: "starts_on", label: "From", kind: "date" },
        { key: "ends_on", label: "To", kind: "date" },
        { key: "budget", label: "Budget", kind: "money", initial: "0" },
        { key: "note", label: "Note", wide: true },
      ]}
      panels={[{
        title: "What it brought", permission: "sales.view_campaign", endpoint: "", query: () => ({}),
        read: { path: (record) => `/api/sales/campaigns/${record.id}/results/`, rows: (data) => [{ ...(data as Row), id: 1 }] },
        columns: [
          { key: "leads", label: "Leads", kind: "quantity", width: "7rem", render: (row) => String(row.leads) },
          { key: "converted", label: "Became customers", kind: "quantity", width: "10rem", render: (row) => String(row.converted) },
          { key: "opportunities", label: "Opportunities", kind: "quantity", width: "9rem", render: (row) => String(row.opportunities) },
          { key: "open_value", label: "In play", kind: "money", width: "11rem", render: (row) => money(row.open_value as string) },
          { key: "won", label: "Won", kind: "quantity", width: "6rem", render: (row) => String(row.won) },
          { key: "won_value", label: "Won, worth", kind: "money", width: "11rem", render: (row) => money(row.won_value as string) },
        ],
      }]}
    />
  );
}
