import { ReportView } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: number };
type Gap = { employee: number; employee_number: string; employee_name: string; days: string[] };

/** Working days of day-rated workers with nothing on the register and no leave: what the pay run refuses on until they are marked. */
export default function AttendanceGaps() {
  return (
    <ReportView<Gap[], Row>
      title="Not yet marked"
      endpoint="/api/hr/attendance/unmarked/"
      params={[
        { key: "start", label: "From", kind: "date", initial: "month-start", required: true },
        { key: "end", label: "To", kind: "date", initial: "today", required: true },
      ]}
      rows={(data) => data.flatMap((gap) => gap.days.map((day) => ({
        id: `${gap.employee}-${day}`, day, employee_number: gap.employee_number, employee_name: gap.employee_name,
      }) as unknown as Row))}
      columns={[
        { key: "day", label: "Day", kind: "date", width: "8rem" },
        { key: "employee_number", label: "No.", width: "7rem" },
        { key: "employee_name", label: "Who" },
      ]}
      empty="Every working day of everyone paid by attendance is marked."
    />
  );
}
