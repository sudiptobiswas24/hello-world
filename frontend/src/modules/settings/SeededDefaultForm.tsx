import { RecordScreen } from "../../views/RecordScreen";

/** One record setup seeded: what it is, and whether the accountant has reviewed it. */
export default function SeededDefaultForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/seeded-defaults/"
      back="/settings/seeded-defaults"
      backLabel="Seeded defaults"
      newTitle="Seeded default"
      heading={(row) => String(row.label ?? "")}
      state={(row) => (row.reviewed_on ? { label: "Reviewed", tone: "done" } : { label: "To review", tone: "draft" })}
      permissions={{}}
      editable={() => false}
      fields={[
        { key: "label", label: "Seeded" },
        { key: "model", label: "Kind" },
        { key: "reviewed_by_name", label: "Reviewed by" },
        { key: "reviewed_on", label: "Reviewed on", kind: "date" },
      ]}
      actions={[
        { label: "Mark reviewed", path: "review", permission: "accounting.change_seededdefault", when: (row) => !row.reviewed_on,
          primary: true, done: "Marked reviewed" },
      ]}
    />
  );
}
