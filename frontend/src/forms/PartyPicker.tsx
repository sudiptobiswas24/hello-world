import { RecordPicker } from "./RecordPicker";

interface Party {
  id: number;
  code: string;
  name: string;
}

export type PartyRole = "customer" | "vendor";

/** A customer or a vendor, by name or code: only active ones in that role. */
export function PartyPicker({ role, value, onChange, invalid, id, disabled }: {
  role: PartyRole;
  value: number | null;
  onChange: (id: number | null) => void;
  invalid?: boolean;
  id?: string;
  disabled?: boolean;
}) {
  return (
    <RecordPicker<Party>
      id={id}
      endpoint="/api/core/parties/"
      fixed={{ role_assignments__role: role, is_active: "true" }}
      value={value}
      onChange={(next) => onChange(next)}
      label={(row) => row.name}
      detail={(row) => row.code}
      placeholder={`Type a ${role}'s name or code`}
      invalid={invalid}
      disabled={disabled}
    />
  );
}
