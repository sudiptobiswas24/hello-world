import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { list, withQuery, type Query } from "../api/client";
import { useAccess } from "../auth/me";
import { count } from "../lib/format";

/**
 * One count on a record's header that opens the list it counts: a
 * customer's orders, a vendor's bills not yet paid. `screen` is the list
 * screen (its path in the application) asked with the same narrowing as
 * the count, so what the button says is what the list then shows.
 */
export interface SmartDef {
  label: string;
  endpoint: string;
  query: Query;
  permission: string;
  screen: string;
  /** Shown red when there are any: late, owed, waiting. */
  urgent?: boolean;
}

function SmartButton({ def }: { def: SmartDef }) {
  const total = useQuery({
    queryKey: ["count", def.endpoint, def.query],
    // A list answers how many it holds in its header; one row is all that is read.
    queryFn: async ({ signal }) => (await list(def.endpoint, { ...def.query, page_size: 1 }, signal)).total,
    staleTime: 30_000,
  });
  const many = (total.data ?? 0) > 0;
  return (
    <Link to={withQuery(def.screen, def.query)} className={`smart${def.urgent && many ? " bad" : ""}`}>
      <strong>{total.isPending ? "…" : total.isError ? "—" : count(total.data ?? 0)}</strong>
      <span>{def.label}</span>
    </Link>
  );
}

/** The record's related documents as counts, each offered only to whoever may read that list. */
export function SmartButtons({ buttons }: { buttons: SmartDef[] }) {
  const { can } = useAccess();
  const offered = buttons.filter((button) => can(button.permission));
  if (offered.length === 0) return null;
  return <nav className="smart-buttons" aria-label="Related">{offered.map((def) => <SmartButton key={def.label} def={def} />)}</nav>;
}
