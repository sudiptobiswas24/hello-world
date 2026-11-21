import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

// One record, made the first time it is read.
const columns: Column<Row>[] = [{ key: "id", label: "Settings", render: () => "Quality settings" }];

export default function QualitySettingsList() {
  return (
    <ListView<Row>
      title="Quality settings"
      noun={["quality settings", "quality settings"]}
      endpoint="/api/quality/quality-settings/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/quality-settings/${row.id}`}
      searchHint="Nothing to search"
    />
  );
}
