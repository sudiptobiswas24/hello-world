import type { Column } from "../../views/DataTable";
import { ReportView } from "../../views/ReportView";

interface Level { item: number; sku: string; level: number; [key: string]: unknown }
interface Answer { levels: Level[]; cuts: { item: string; parent: string }[] }

/**
 * Where every item sits in the explosion: 0 is sold, each level down is
 * made into the one above. A cycle in the recipes is cut and named, since
 * planning cannot explode through it.
 */
export default function Levels() {
  const columns: Column<Level>[] = [
    { key: "level", label: "Level", kind: "quantity", width: "6rem" },
    { key: "sku", label: "Item" },
  ];
  return (
    <ReportView<Answer, Level>
      title="Explosion levels"
      endpoint="/api/planning/levels/"
      params={[]}
      rows={(data) => data.levels}
      columns={columns}
      empty="No recipes yet."
      above={(data) => (data.cuts.length ? (
        <p className="note" role="note">
          Recipes that go round in a circle, cut where planning stops:{" "}
          {data.cuts.map((cut) => `${cut.item} under ${cut.parent}`).join("; ")}.
        </p>
      ) : null)}
    />
  );
}
