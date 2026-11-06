import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { list } from "../../api/client";
import { useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { count, dateTime } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Finding {
  key: string;
  label: string;
  ok: boolean | null;
  count: number;
  detail: string;
  rows: { label: string; href: string }[];
  href: string;
}
interface Backups { configured: boolean; directory: string; newest: string; age_hours: number | null; stale: boolean }
interface Server {
  ok: boolean;
  problems: string[];
  database: string;
  migrations_pending: number | null;
  disk_free_mb: number;
  disk_low: boolean;
  backups: Backups;
}
interface Check { key: string; label: string; count: number; href: string }
interface Report { day: string; checked_at: string; books: Finding[]; server: Server; checks: Check[] }

function verdict(finding: Finding) {
  if (finding.ok === true) return <span className="pill pill-done">Agrees</span>;
  if (finding.ok === null) return <span className="pill pill-warn">Not proved</span>;
  return <span className="pill pill-bad">Does not agree</span>;
}

/**
 * Whether the system agrees with itself: the books against themselves
 * (each control account against its documents, the stock against the
 * ledger, closed months, invoice numbers), the server (database, schema,
 * disk, last night's backup), and every morning check with its count,
 * zero included, so what was looked at is on the page as well as what
 * was found. Problems people hit are listed on their own screen.
 */
export default function Health() {
  const { can } = useAccess();
  const report = useGet<Report>("/api/web/health/");
  // How many are open: the count the server keeps, not the rows of one page.
  const problems = useQuery({
    queryKey: ["count", "/api/core/errors/", { resolved_at__isnull: "true" }],
    queryFn: async ({ signal }) => (await list("/api/core/errors/", { resolved_at__isnull: "true", page_size: 1 }, signal)).total,
    enabled: can("core.view_servererror"),
    staleTime: 30_000,
  });
  const data = report.data;
  const server = data?.server;
  const unresolved = problems.data ?? 0;

  return (
    <section className="report">
      <header className="list-head">
        <h1>Health</h1>
        <ActionButton onClick={() => void report.refetch()} pending={report.isFetching}>Check again</ActionButton>
      </header>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <>
          <p className="muted">
            {data ? `Checked ${dateTime(data.checked_at)}. ` : "Checking… "}
            Each line is a question the books can answer about themselves; one that does not agree carries forward until it is found.
          </p>
          <h2 className="section-title">The books</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Check</th><th scope="col">Result</th><th scope="col">Detail</th></tr></thead>
              <tbody>
                {!data ? <tr className="skeleton"><td colSpan={3}><span /></td></tr> : data.books.map((finding) => (
                  <tr key={finding.key} className={finding.ok === false ? "bad" : ""}>
                    <td>{finding.href ? <Link to={finding.href}>{finding.label}</Link> : finding.label}</td>
                    <td>{verdict(finding)}</td>
                    <td>
                      {finding.detail}
                      {finding.rows.length > 0 && (
                        <ul>
                          {finding.rows.map((row, index) => (
                            <li key={index}>{row.href ? <Link to={row.href}>{row.label}</Link> : row.label}</li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2 className="section-title">The server</h2>
          {server && (
            <div className="tiles">
              <div className={`tile${server.database === "ok" ? "" : " bad"}`}>
                <span>Database</span><strong>{server.database === "ok" ? "Answers" : "Down"}</strong>
                {server.database !== "ok" && <small>{server.database}</small>}
              </div>
              <div className={`tile${server.migrations_pending ? " bad" : ""}`}>
                <span>Schema</span><strong>{server.migrations_pending ? `${count(server.migrations_pending)} behind` : "Current"}</strong>
                <small>{server.migrations_pending ? "Restart the application to apply them" : "Every migration applied"}</small>
              </div>
              <div className={`tile${server.disk_low ? " bad" : ""}`}>
                <span>Disk free</span><strong>{count(server.disk_free_mb)} MB</strong>
                {server.disk_low && <small>Under a gigabyte: make room</small>}
              </div>
              <div className={`tile${server.backups.configured && server.backups.stale ? " bad" : ""}`}>
                <span>Last backup</span>
                <strong>
                  {!server.backups.configured ? "Not checked" : server.backups.newest
                    ? `${server.backups.age_hours ?? "?"} h ago` : "None found"}
                </strong>
                <small>
                  {!server.backups.configured ? "BACKUP_DIR is not set on this server"
                    : server.backups.newest || server.backups.directory}
                </small>
              </div>
            </div>
          )}

          {can("core.view_servererror") && (
            <>
              <h2 className="section-title">Problems people hit</h2>
              <p>
                {problems.isPending ? "…" : unresolved > 0
                  ? <Link to="/settings/problems?resolved_at__isnull=true" className="bad">{count(unresolved)} not yet dealt with</Link>
                  : <Link to="/settings/problems">None open</Link>}
              </p>
            </>
          )}

          <h2 className="section-title">The morning checks</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Check</th><th scope="col" className="k-quantity">Found</th></tr></thead>
              <tbody>
                {!data ? <tr className="skeleton"><td colSpan={2}><span /></td></tr> : data.checks.map((check) => (
                  <tr key={check.key} className={check.count ? "bad" : ""}>
                    <td><Link to={check.href}>{check.label}</Link></td>
                    <td className="k-quantity">{check.count ? count(check.count) : <span className="muted">none</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
