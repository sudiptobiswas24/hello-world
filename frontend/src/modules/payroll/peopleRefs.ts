import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

export const DEPARTMENT: FieldDef["ref"] = {
  endpoint: "/api/hr/departments/", permission: "hr.view_department", label: (row: Row) => String(row.name || row.code),
};
export const OPENING: FieldDef["pick"] = {
  endpoint: "/api/hr/job-openings/", permission: "hr.view_jobopening", label: (row: Row) => String(row.title),
};
export const SOURCES: [string, string][] = [
  ["walk_in", "Walk-in"], ["referral", "Referral"], ["agency", "Agency"], ["portal", "Job portal"], ["other", "Other"],
];
export const STAGES: [string, string][] = [
  ["applied", "Applied"], ["screening", "Screening"], ["interview", "Interview"], ["offered", "Offered"],
  ["hired", "Hired"], ["rejected", "Rejected"],
];
