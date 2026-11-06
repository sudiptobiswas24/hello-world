import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { readAgainAfter } from "./hooks";

/** A read the test answers by hand, as a slow server would. */
function slowServer() {
  const pending: ((value: string) => void)[] = [];
  let asked = 0;
  const queryFn = () => {
    asked += 1;
    return new Promise<string>((resolve) => pending.push(resolve));
  };
  return { queryFn, pending, asked: () => asked };
}

describe("reading again after a write", () => {
  it("asks again when the first read was still on its way", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const server = slowServer();
    const observer = new QueryObserver<string>(client, { queryKey: ["get", "/api/gst/gstr2b/match/"], queryFn: server.queryFn });
    const stop = observer.subscribe(() => undefined);
    expect(server.asked()).toBe(1);

    // The 2B is kept while the match, asked before it, is still loading.
    const refreshed = readAgainAfter(client, "/api/gst/gstr2b/upload/");
    server.pending[0]!("before the write");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(server.asked()).toBe(2);
    server.pending[1]!("after the write");
    await refreshed;

    expect(observer.getCurrentResult().data).toBe("after the write");
    stop();
  });

  it("reads again a query that already has an answer", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const server = slowServer();
    const observer = new QueryObserver<string>(client, { queryKey: ["get", "/api/x/"], queryFn: server.queryFn });
    const stop = observer.subscribe(() => undefined);
    server.pending[0]!("first");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(observer.getCurrentResult().data).toBe("first");

    const refreshed = readAgainAfter(client, "/api/y/");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(server.asked()).toBe(2);
    server.pending[1]!("second");
    await refreshed;
    expect(observer.getCurrentResult().data).toBe("second");
    stop();
  });

  it("leaves who is signed in alone", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const server = slowServer();
    const observer = new QueryObserver<string>(client, { queryKey: ["me"], queryFn: server.queryFn });
    const stop = observer.subscribe(() => undefined);
    server.pending[0]!("someone");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await readAgainAfter(client, "/api/y/");
    expect(server.asked()).toBe(1);
    stop();
  });
});
