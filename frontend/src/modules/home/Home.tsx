import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { get, list, type Query } from "../../api/client";
import { MODULES, offered, screenUrl } from "../../app/registry";
import { useAccess } from "../../auth/me";
import { count, date } from "../../lib/format";
import type { FollowUpRow } from "../../views/Chatter";
import { Icon } from "../../shell/Icon";

function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

interface Waiting {
  label: string;
  hint: string;
  endpoint: string;
  query: Query;
  href: string;
  permission: string;
  urgent?: boolean;
}

/**
 * What is waiting on someone today: each a count the server keeps, and a
 * link to exactly those documents. Only what the person's roles may open.
 */
const WAITING: Waiting[] = [
  { label: "Orders to ship", hint: "Confirmed, not all dispatched", endpoint: "/api/sales/sales-orders/",
    query: { status: "confirmed", to_ship: "true" }, href: "/sales/orders?status=confirmed&to_ship=true", permission: "sales.view_salesorder" },
  { label: "Deliveries not yet shipped", hint: "Drafted, waiting on the gate", endpoint: "/api/sales/deliveries/",
    query: { posted: "false" }, href: "/sales/deliveries?posted=false", permission: "sales.view_delivery" },
  { label: "Invoices to post", hint: "Drafts nobody has posted", endpoint: "/api/sales/invoices/",
    query: { posted: "false" }, href: "/sales/invoices?posted=false", permission: "sales.post_invoice" },
  { label: "Invoices not yet paid", hint: "Posted, money still owed", endpoint: "/api/sales/invoices/",
    query: { open: "true" }, href: "/sales/invoices?open=true", permission: "sales.view_invoice", urgent: true },
  { label: "Quotes awaiting an answer", hint: "Sent, neither accepted nor declined", endpoint: "/api/sales/quotations/",
    query: { status: "sent" }, href: "/sales/quotations?status=sent", permission: "sales.view_quotation" },
];

function WaitingTile({ item }: { item: Waiting }) {
  const total = useQuery({
    queryKey: ["count", item.endpoint, item.query],
    queryFn: async ({ signal }) => (await list(item.endpoint, { ...item.query, page_size: 1 }, signal)).total,
    staleTime: 30_000,
  });
  return (
    <Link to={item.href} className={`tile link${item.urgent && (total.data ?? 0) > 0 ? " bad" : ""}`}>
      <span>{item.label}</span>
      <strong>{total.isPending ? "…" : total.isError ? "—" : count(total.data)}</strong>
      <small>{item.hint}</small>
    </Link>
  );
}

interface InboxRow { key: string; label: string; count: number; href: string }

/** The morning checks, counted now for this login: what fell due that is theirs to act on. */
function Inbox() {
  const rows = useQuery({
    queryKey: ["inbox"],
    queryFn: async ({ signal }) => (await get<{ rows: InboxRow[] }>("/api/web/inbox/", undefined, signal)).rows,
    staleTime: 60_000,
  });
  if (!rows.data?.length) return null;
  return (
    <>
      <h2 className="section-title">Yours to act on today</h2>
      <div className="tiles">
        {rows.data.map((row) => (
          <Link key={row.key} to={row.href} className="tile link bad">
            <span>{row.label}</span>
            <strong>{count(row.count)}</strong>
          </Link>
        ))}
      </div>
    </>
  );
}

/** The follow-ups planned for this login and not yet done, late ones first, each leading to its record. */
function MyFollowUps() {
  const rows = useQuery({
    queryKey: ["get", "/api/core/follow-ups/", { mine: "true" }],
    queryFn: ({ signal }) => get<FollowUpRow[]>("/api/core/follow-ups/", { mine: "true" }, signal),
    staleTime: 30_000,
  });
  if (!rows.data?.length) return null;
  return (
    <>
      <h2 className="section-title">Your follow-ups</h2>
      <table className="data" aria-label="Your follow-ups">
        <tbody>
          {rows.data.map((row) => (
            <tr key={row.id} className={row.state === "overdue" ? "bad" : undefined}>
              <td>{date(row.due_on)}{row.state !== "planned" && <span className={`pill ${row.state === "overdue" ? "bad" : "info"}`}>{row.state === "overdue" ? "Late" : "Today"}</span>}</td>
              <td>{row.kind_label}</td>
              <td>{row.link ? <Link to={row.link}>{row.summary}</Link> : row.summary}</td>
              <td className="muted">{row.record_kind} · {row.record}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/** Where a person starts: what is waiting on them, then everything their roles open. */
export default function Home() {
  const { me, can } = useAccess();
  const modules = MODULES.map((module) => ({
    ...module,
    screens: module.screens.filter((screen) => offered(screen, can)),
  })).filter((module) => module.screens.length > 0);
  const waiting = WAITING.filter((item) => can(item.permission));

  return (
    <section className="home">
      <h1>{greeting(new Date().getHours())}, {me.name.split(" ")[0]}</h1>
      <Inbox />
      <MyFollowUps />
      {waiting.length > 0 && (
        <>
          <h2 className="section-title">Waiting today</h2>
          <div className="tiles">{waiting.map((item) => <WaitingTile key={item.label} item={item} />)}</div>
        </>
      )}
      {modules.length === 0 ? (
        <p className="muted">
          {me.roles.length === 0 && !me.is_superuser
            ? "Your account has no role yet, so there is nothing here to open. Ask your administrator to give you the role for your job."
            : `Nothing in this application is open to your role (${me.roles.join(", ")}) yet.`}
        </p>
      ) : (
        <>
          <h2 className="section-title">Everything</h2>
          <div className="cards">
            {modules.map((module) => (
              <article key={module.key} className="card">
                <h2><Icon name={module.icon} /> {module.label}</h2>
                <ul>
                  {module.screens.map((screen) => (
                    <li key={screen.path}><Link to={screenUrl(module, screen)}>{screen.label}</Link></li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
