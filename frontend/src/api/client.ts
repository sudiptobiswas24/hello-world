/**
 * Every conversation with the server goes through here.
 *
 * Same origin as the page, so the session cookie that signed the person
 * in travels with each request by itself. Django refuses a write without
 * the CSRF token from its cookie; every write sends it. Whatever goes
 * wrong comes back as one ApiError a screen can show, never a raw
 * response or a stack trace.
 */

export type FieldErrors = Record<string, string[]>;

export type ErrorKind =
  | "offline" // the request never reached the server
  | "signed-out" // the session ended; the browser is on its way to sign in
  | "forbidden" // signed in, but this person may not do this
  | "not-found"
  | "invalid" // the server refused the input, and said why
  | "conflict"
  | "server"; // the server failed; nothing the person did

export class ApiError extends Error {
  readonly status: number;
  readonly kind: ErrorKind;
  readonly messages: string[];
  readonly fields: FieldErrors;

  constructor(status: number, kind: ErrorKind, messages: string[], fields: FieldErrors = {}) {
    super(messages[0] ?? kind);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.messages = messages;
    this.fields = fields;
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function csrfToken(cookies: string = document.cookie): string {
  const match = cookies.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1] ?? "") : "";
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === "") continue;
    params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}${path.includes("?") ? "&" : "?"}${text}` : path;
}

/**
 * DRF says what is wrong in several shapes: {"detail": "..."}, a list of
 * sentences, {"field": ["..."]}, and nested lists for document lines.
 * Flattened to sentences to show and a map from field path to sentences,
 * so a form can put each next to the box it is about.
 */
export function readErrors(body: unknown): { messages: string[]; fields: FieldErrors } {
  const messages: string[] = [];
  const fields: FieldErrors = {};
  const walk = (value: unknown, path: string) => {
    if (value === null || value === undefined) return;
    if (typeof value === "string") {
      if (!path || path === "detail" || path === "non_field_errors") messages.push(value);
      else (fields[path] ??= []).push(value);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) => {
        const nested = typeof item === "object" && item !== null;
        walk(item, nested ? `${path}[${index}]` : path);
      });
      return;
    }
    if (typeof value === "object") {
      for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
        walk(item, path ? `${path}.${key}` : key);
      }
    }
  };
  walk(body, "");
  for (const [path, list] of Object.entries(fields)) {
    for (const message of list) messages.push(`${labelFor(path)}: ${message}`);
  }
  return { messages, fields };
}

function labelFor(path: string): string {
  return path
    .replace(/\[(\d+)\]/g, (_, index: string) => ` ${Number(index) + 1}`)
    .replace(/[._]/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

const PLAIN: Record<number, string> = {
  404: "That is not here any more. It may have been deleted, or the address is wrong.",
  405: "The server does not allow that here.",
  409: "Someone else changed this at the same time. Reload and try again.",
  413: "That is too large to send.",
  429: "Too many attempts. Wait a little and try again.",
};

const SERVER =
  "Something went wrong on the server. It has been logged. Try again, and tell the administrator if it keeps happening.";

let signingIn = false;

function sendToSignIn(): void {
  if (signingIn) return;
  signingIn = true;
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/accounts/login/?next=${encodeURIComponent(here)}`);
}

/**
 * A 403 means one of two things, and DRF answers both the same way: the
 * session ended, or this person may not. Asking who is signed in tells
 * them apart without guessing from the wording.
 */
async function stillSignedIn(): Promise<boolean> {
  try {
    const response = await fetch("/api/core/me/", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    return response.ok;
  } catch {
    return true; // offline: not a reason to throw away the page
  }
}

export interface Reply<T> {
  data: T;
  headers: Headers;
  status: number;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Query;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<Reply<T>> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (UNSAFE.has(method)) headers["X-CSRFToken"] = csrfToken();

  let response: Response;
  try {
    response = await fetch(withQuery(path, options.query), {
      method,
      headers,
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "offline", [
      "The server could not be reached. Check the network connection and try again.",
    ]);
  }

  const type = response.headers.get("Content-Type") ?? "";
  let body: unknown = null;
  if (response.status !== 204) {
    const text = await response.text();
    if (text && type.includes("json")) {
      try {
        body = JSON.parse(text);
      } catch {
        body = null;
      }
    } else {
      body = text;
    }
  }

  if (response.ok) return { data: body as T, headers: response.headers, status: response.status };

  const status = response.status;
  if (status === 401 || status === 403) {
    if (status === 401 || !(await stillSignedIn())) {
      sendToSignIn();
      throw new ApiError(status, "signed-out", ["Your session has ended. Taking you to sign in."]);
    }
    const { messages } = readErrors(body);
    throw new ApiError(status, "forbidden", messages.length ? messages : ["You do not have permission to do that."]);
  }
  if (status >= 500) throw new ApiError(status, "server", [SERVER]);
  if (status === 400) {
    const { messages, fields } = readErrors(body);
    throw new ApiError(status, "invalid", messages.length ? messages : ["The server refused that."], fields);
  }
  const kind: ErrorKind = status === 404 ? "not-found" : status === 409 ? "conflict" : "invalid";
  const { messages } = readErrors(body);
  throw new ApiError(status, kind, messages.length ? messages : [PLAIN[status] ?? `The server answered ${status}.`]);
}

export async function get<T>(path: string, query?: Query, signal?: AbortSignal): Promise<T> {
  return (await request<T>(path, { query, signal })).data;
}

export async function send<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  return (await request<T>(path, { method, body })).data;
}

export interface Page<T> {
  rows: T[];
  total: number;
  page: number;
  pageSize: number;
}

/**
 * One page of a list. The server sends the rows as the body and the
 * count in X-Total-Count; a list it does not page (a short master list)
 * has no header, and its length is the count.
 */
export async function list<T>(path: string, query?: Query, signal?: AbortSignal): Promise<Page<T>> {
  const reply = await request<T[]>(path, { query, signal });
  const rows = Array.isArray(reply.data) ? reply.data : [];
  const header = reply.headers.get("X-Total-Count");
  const total = header === null ? rows.length : Number(header);
  return {
    rows,
    total: Number.isFinite(total) ? total : rows.length,
    page: Number(reply.headers.get("X-Page") ?? 1) || 1,
    pageSize: Number(reply.headers.get("X-Page-Size") ?? rows.length) || rows.length,
  };
}

export function resetSignInForTests(): void {
  signingIn = false;
}
