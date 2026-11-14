import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

// One record, made the first time it is read.
const columns: Column<Row>[] = [{ key: "id", label: "Settings", render: () => "Manufacturing settings" }];

export default function ManufacturingSettingsList() {
  return (
    <ListView<Row>
      title="Manufacturing settings"
      noun={["manufacturing settings", "manufacturing settings"]}
      endpoint="/api/manufacturing/manufacturing-settings/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/manufacturing-settings/${row.id}`}
      searchHint="Nothing to search"
    />
  );
}
