import { RecordScreen } from "../../views/RecordScreen";

/** How each kind of document is numbered: its prefix, padding and the number it gives next. A number can be raised, never lowered. */
export default function DocumentSequenceForm() {
  return (
    <RecordScreen
      endpoint="/api/core/document-sequences/"
      back="/settings/numbering"
      backLabel="Document numbering"
      newTitle="New sequence"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      permissions={{ add: "core.add_documentsequence", change: "core.change_documentsequence" }}
      fields={[
        { key: "code", label: "Code", createOnly: true, hint: "What the documents ask for, e.g. sales.invoice; fixed once made" },
        { key: "name", label: "Name" },
        { key: "prefix", label: "Prefix", hint: "e.g. INV-" },
        { key: "suffix", label: "Suffix" },
        { key: "padding", label: "Padding", kind: "integer", initial: 5 },
        { key: "next_number", label: "Next number", kind: "integer", initial: 1, hint: "Raised, never lowered; once numbers are given by year, each year keeps its own" },
        { key: "include_year", label: "Include year", kind: "bool", initial: true, hint: "Needed while the count starts again each year" },
        { key: "reset_yearly", label: "Reset yearly", kind: "bool", initial: true },
      ]}
    />
  );
}
