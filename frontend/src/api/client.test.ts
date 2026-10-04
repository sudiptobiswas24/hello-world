import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, csrfToken, list, readErrors, request, resetSignInForTests, send, withQuery } from "./client";

type Call = { url: string; init: RequestInit };

function reply(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  const text = body === null ? "" : typeof body === "string" ? body : JSON.stringify(body);
  return new Response(status === 204 ? null : text, {
    status,
    headers: { "Content-Type": typeof body === "string" ? "text/html" : "application/json", ...headers },
  });
}

let calls: Call[];
let answers: Response[];

beforeEach(() => {
  calls = [];
  answers = [];
  resetSignInForTests();
  document.cookie = "csrftoken=tok%3D1";
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const next = answers.shift();
    if (!next) throw new Error("no answer queued");
    return next;
  }));
  Object.defineProperty(window, "location", {
    value: { pathname: "/app/sales/invoices", search: "?q=bolt", assign: vi.fn() },
    writable: true,
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("the request", () => {
  it("reads without the CSRF token and with the session cookie", async () => {
    answers.push(reply(200, { ok: true }));
    await request("/api/x/");
    const headers = calls[0]!.init.headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBeUndefined();
    expect(calls[0]!.init.credentials).toBe("same-origin");
  });

  it("writes with the token from the cookie, decoded", async () => {
    answers.push(reply(201, { id: 3 }));
    await send("POST", "/api/x/", { a: 1 });
    const headers = calls[0]!.init.headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBe("tok=1");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(calls[0]!.init.body).toBe('{"a":1}');
  });

  it("leaves empty parameters out of the address", () => {
    expect(withQuery("/api/x/", { search: "", page: 2, posted: false, none: null })).toBe(
      "/api/x/?page=2&posted=false",
    );
  });

  it("finds the token among other cookies", () => {
    expect(csrfToken("a=1; csrftoken=abc; b=2")).toBe("abc");
    expect(csrfToken("a=1")).toBe("");
  });

  it("returns nothing for 204", async () => {
    answers.push(reply(204, null));
    expect((await request("/api/x/", { method: "DELETE" })).data).toBeNull();
  });
});

describe("a page of a list", () => {
  it("takes the count from the header, not the rows", async () => {
    answers.push(reply(200, [{ id: 1 }, { id: 2 }], { "X-Total-Count": "25000", "X-Page": "3", "X-Page-Size": "2" }));
    const page = await list("/api/x/");
    expect(page).toEqual({ rows: [{ id: 1 }, { id: 2 }], total: 25000, page: 3, pageSize: 2 });
  });

  it("counts the rows of a list the server does not page", async () => {
    answers.push(reply(200, [{ id: 1 }]));
    expect((await list("/api/x/")).total).toBe(1);
  });
});

describe("what went wrong", () => {
  it("puts each field's refusal under its field and in the sentences", () => {
    const read = readErrors({ quantity: ["Must be positive."], non_field_errors: ["Posted."] });
    expect(read.fields).toEqual({ quantity: ["Must be positive."] });
    expect(read.messages).toEqual(["Posted.", "Quantity: Must be positive."]);
  });

  it("names the line a refusal is about", () => {
    const read = readErrors({ lines: [{}, { unit_price: ["Required."] }] });
    expect(read.fields).toEqual({ "lines[1].unit_price": ["Required."] });
    expect(read.messages).toEqual(["Lines 2 unit price: Required."]);
  });

  it("reads a bare list and a detail", () => {
    expect(readErrors(["One.", "Two."]).messages).toEqual(["One.", "Two."]);
    expect(readErrors({ detail: "No." }).messages).toEqual(["No."]);
  });

  it("is a 400 the form can show", async () => {
    answers.push(reply(400, { quantity: ["Must be positive."] }));
    const error = await request("/api/x/", { method: "POST", body: {} }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("invalid");
    expect((error as ApiError).fields.quantity).toEqual(["Must be positive."]);
  });

  it("tells a refusal from a session that ended", async () => {
    answers.push(reply(403, { detail: "You do not have permission to perform this action." }));
    answers.push(reply(200, { id: 1 })); // /me still answers: signed in
    const refused = (await request("/api/x/").catch((e: unknown) => e)) as ApiError;
    expect(refused.kind).toBe("forbidden");
    expect(calls[1]!.url).toBe("/api/core/me/");
    expect(window.location.assign).not.toHaveBeenCalled();

    answers.push(reply(403, { detail: "Authentication credentials were not provided." }));
    answers.push(reply(403, { detail: "Authentication credentials were not provided." }));
    const ended = (await request("/api/x/").catch((e: unknown) => e)) as ApiError;
    expect(ended.kind).toBe("signed-out");
    expect(window.location.assign).toHaveBeenCalledWith(
      "/accounts/login/?next=%2Fapp%2Fsales%2Finvoices%3Fq%3Dbolt",
    );
  });

  it("never shows a server's insides", async () => {
    answers.push(reply(500, "<html>Traceback (most recent call last)...</html>"));
    const error = (await request("/api/x/").catch((e: unknown) => e)) as ApiError;
    expect(error.kind).toBe("server");
    expect(error.message).not.toContain("Traceback");
  });

  it("says so when the server cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    const error = (await request("/api/x/").catch((e: unknown) => e)) as ApiError;
    expect(error.kind).toBe("offline");
  });

  it("lets a cancelled request stay cancelled", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new DOMException("aborted", "AbortError");
    }));
    const error = await request("/api/x/").catch((e: unknown) => e);
    expect((error as DOMException).name).toBe("AbortError");
  });
});
