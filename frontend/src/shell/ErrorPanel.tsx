import { ApiError } from "../api/client";

const TITLES: Record<string, string> = {
  offline: "Cannot reach the server",
  forbidden: "Not allowed",
  "not-found": "Not found",
  invalid: "Refused",
  conflict: "Changed by someone else",
  server: "The server ran into a problem",
  "signed-out": "Signing you in again",
};

/** What went wrong, in the server's own words where it gave them, and a way on. */
export function ErrorPanel({ error, retry }: { error: unknown; retry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  const title = api ? TITLES[api.kind] ?? "Something went wrong" : "Something went wrong";
  const messages = api
    ? api.messages
    : ["The screen ran into a problem it did not expect. Reload the page; if it happens again, tell the administrator."];
  return (
    <div className="error-panel" role="alert">
      <h2>{title}</h2>
      {messages.map((message) => <p key={message}>{message}</p>)}
      {retry && api?.kind !== "signed-out" && api?.kind !== "forbidden" && (
        <button type="button" onClick={retry}>Try again</button>
      )}
    </div>
  );
}
