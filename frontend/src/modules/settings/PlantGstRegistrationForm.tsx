import { RecordScreen } from "../../views/RecordScreen";

const POSITION = {
  endpoint: "/api/accounting/fiscal-positions/",
  label: (row: Record<string, unknown>) => `${String(row.code ?? "")} ${String(row.name ?? "")}`.trim(),
  permission: "accounting.view_fiscalposition",
};

/** The plant's GST registration. The state is read off the GSTIN; a new GSTIN once tax is recorded is refused. */
export default function PlantGstRegistrationForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/gst-settings/"
      back="/settings/plant-gst"
      backLabel="Plant's GST registration"
      newTitle="The plant's GST registration"
      heading={(row) => `GST ${String(row.gstin ?? "")}`}
      permissions={{ add: "accounting.add_gstsettings", change: "accounting.change_gstsettings" }}
      fields={[
        { key: "gstin", label: "GSTIN", hint: "Fifteen characters; the first two are the state" },
        { key: "state_name", label: "State", readOnly: true },
        { key: "interstate_position", label: "Across a state line", kind: "ref", ref: POSITION,
          hint: "INTER, as setup_indian_books seeds it: CGST and SGST to IGST at the whole rate" },
        { key: "is_active", label: "In force", kind: "bool", initial: true },
        { key: "b2cl_limit", label: "B2CL limit", kind: "money", initial: "100000.00", hint: "1,00,000 since August 2024" },
        { key: "einvoicing_from", label: "E-invoicing from", kind: "date",
          hint: "The first invoice date that must carry an IRN: from when turnover passed 5 crore" },
        { key: "eway_bill_limit", label: "E-way bill above", kind: "money", initial: "50000.00" },
        { key: "job_work_sac", label: "Job-work SAC", hint: "Starts 99: conversion is a service" },
      ]}
    />
  );
}
