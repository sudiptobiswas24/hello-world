import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "opened_on", label: "Books opened on", kind: "date", width: "10rem" },
  { key: "opening_account_name", label: "Opening balances stand against" },
];

/**
 * The day the books opened and the equity account opening balances stand against (O251). One row, set before an
 * asset is typed in or the old system's balances are imported: both refuse until it is, and name this screen.
 */
export default function BooksOpenings() {
  return (
    <ListView<Row>
      title="Books opening"
      noun={["books opening", "books openings"]}
      endpoint="/api/accounting/books-opening/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/books-opening/${row.id}`}
      searchHint="Nothing to search"
      emptyHint="Not set yet: no asset can be typed in and no opening balance imported until the day the books opened is set here."
      create={{ href: "/settings/books-opening/new", permission: "accounting.add_booksopening" }}
    />
  );
}
