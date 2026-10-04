import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";

import { get } from "../api/client";

export interface Me {
  id: number;
  username: string;
  name: string;
  is_superuser: boolean;
  roles: string[];
  permissions: string[];
  company: string;
}

interface Access {
  me: Me;
  /** Whether this person holds the permission ("sales.view_invoice"). */
  can: (permission: string) => boolean;
  /** Whether they hold any of them. */
  canAny: (permissions: readonly string[]) => boolean;
}

const AccessContext = createContext<Access | null>(null);

/**
 * Who is signed in, read once when the application opens and kept.
 *
 * It decides what is offered, never what is allowed: the server checks
 * every request against the same permissions whatever is shown here.
 */
export function AccessProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const { data, error } = useQuery({
    queryKey: ["me"],
    queryFn: ({ signal }) => get<Me>("/api/core/me/", undefined, signal),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  if (error) throw error;
  if (!data) return <>{fallback}</>;
  const held = new Set(data.permissions);
  const can = (permission: string) => data.is_superuser || held.has(permission);
  const value: Access = { me: data, can, canAny: (list) => list.some(can) };
  return <AccessContext.Provider value={value}>{children}</AccessContext.Provider>;
}

export function useAccess(): Access {
  const access = useContext(AccessContext);
  if (!access) throw new Error("useAccess outside AccessProvider");
  return access;
}
