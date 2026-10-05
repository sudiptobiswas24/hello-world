import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useToast } from "../shell/Toasts";
import { ApiError, get, list, send, type Query } from "./client";

/** One document, by id. */
export function useRecord<T>(endpoint: string, id: string | number | undefined) {
  return useQuery<T, ApiError>({
    queryKey: ["record", endpoint, String(id)],
    queryFn: ({ signal }) => get<T>(`${endpoint}${id}/`, undefined, signal),
    enabled: id !== undefined && id !== "new",
    staleTime: 5_000,
  });
}

/**
 * A short master list read whole and kept: taxes, warehouses, currencies,
 * payment terms. Not for anything that grows with the business.
 */
export function useReference<T>(endpoint: string, query?: Query) {
  return useQuery<T[], ApiError>({
    queryKey: ["reference", endpoint, query],
    queryFn: async ({ signal }) => (await list<T>(endpoint, { page_size: 500, ...query }, signal)).rows,
    staleTime: 10 * 60_000,
  });
}

/**
 * One page of a list with how many there are in all, for a panel that
 * must say when it is showing only part: a pay run of 600 people, a
 * receipt applied to 250 invoices.
 */
export function usePage<T>(endpoint: string, query: Query, page: number, size: number, enabled = true) {
  return useQuery<{ rows: T[]; total: number }, ApiError>({
    queryKey: ["list", endpoint, query, page, size],
    queryFn: async ({ signal }) => {
      const result = await list<T>(endpoint, { ...query, page, page_size: size }, signal);
      return { rows: result.rows, total: result.total };
    },
    enabled,
    staleTime: 5_000,
    placeholderData: (previous) => previous,
  });
}

/** The rows of any list, for a panel on another document. */
export function useRows<T>(endpoint: string, query: Query, enabled = true) {
  return useQuery<T[], ApiError>({
    queryKey: ["list", endpoint, query],
    queryFn: async ({ signal }) => (await list<T>(endpoint, { page_size: 200, ...query }, signal)).rows,
    enabled,
    staleTime: 5_000,
  });
}

export interface ActOptions<R> {
  /** What to tell the person when it worked. */
  done?: string | ((result: R) => string);
  onDone?: (result: R) => void;
}

/**
 * Something the person does to a document: confirm, post, credit. The
 * button is held while it runs (a second click does nothing), the
 * outcome is announced, and every list and document is read again,
 * because one action moves several (posting an invoice changes the
 * order's billed figures and the customer's balance).
 */
export function useAct<R = unknown>() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const mutation = useMutation<R, ApiError, { method: "POST" | "PATCH" | "DELETE" | "PUT"; path: string; body?: unknown; options?: ActOptions<R> }>({
    mutationFn: ({ method, path, body }) => send<R>(method, path, body),
    onSuccess: async (result, { options }) => {
      await queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "me" && query.queryKey[0] !== "reference" });
      const done = options?.done;
      if (done) toast.ok(typeof done === "function" ? done(result) : done);
      options?.onDone?.(result);
    },
    onError: (error) => {
      if (error.kind !== "signed-out") toast.bad(error.messages.join(" "));
    },
  });
  return {
    /** What happened, returned rather than read back from state, which lags a render. */
    run: (method: "POST" | "PATCH" | "DELETE" | "PUT", path: string, body?: unknown, options?: ActOptions<R>) =>
      mutation.mutateAsync({ method, path, body, options }).then(
        (data): Outcome<R> => ({ ok: true, data }),
        (error: ApiError): Outcome<R> => ({ ok: false, error }),
      ),
    pending: mutation.isPending,
  };
}

export type Outcome<R> = { ok: true; data: R } | { ok: false; error: ApiError };

/** Anything the server answers with one object (a report, an approval). */
export function useGet<T>(path: string, query?: Query, enabled = true) {
  return useQuery<T, ApiError>({
    queryKey: ["get", path, query],
    queryFn: ({ signal }) => get<T>(path, query, signal),
    enabled,
    staleTime: 5_000,
  });
}
