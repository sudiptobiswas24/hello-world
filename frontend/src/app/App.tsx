import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, type ReactNode } from "react";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router";

import { ApiError } from "../api/client";
import { AccessProvider } from "../auth/me";
import { ErrorPanel } from "../shell/ErrorPanel";
import { RouteError } from "../shell/RouteError";
import { Shell } from "../shell/Shell";
import { MODULES } from "./registry";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A refused or missing read will not come right by asking again.
      retry: (failures, error) =>
        failures < 2 && !(error instanceof ApiError && ["forbidden", "not-found", "invalid", "signed-out"].includes(error.kind)),
      refetchOnWindowFocus: false,
    },
  },
});

const routes: RouteObject[] = [
  {
    path: "/",
    element: <Shell />,
    errorElement: <RouteError />,
    children: [
      {
        errorElement: <RouteError />,
        children: [
          { index: true, lazy: async () => ({ Component: (await import("../modules/home/Home")).default }) },
          ...MODULES.flatMap((module) =>
            module.screens.map((screen) => ({
              path: `${module.key}/${screen.path}`,
              lazy: async () => ({ Component: (await screen.load()).default }),
            })),
          ),
          {
            path: "*",
            loader: () => {
              throw new Response("", { status: 404 });
            },
          },
        ],
      },
    ],
  },
];

const router = createBrowserRouter(routes, { basename: "/app" });

function Loading() {
  return <div className="boot">Opening…</div>;
}

function BootFailed({ error }: { error: unknown }) {
  return (
    <div className="boot">
      <ErrorPanel error={error} retry={() => window.location.reload()} />
    </div>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Boot />
    </QueryClientProvider>
  );
}

function Boot() {
  return (
    <ErrorBoundaryForBoot>
      <AccessProvider fallback={<Loading />}>
        <RouterProvider router={router} />
      </AccessProvider>
    </ErrorBoundaryForBoot>
  );
}

/** Who is signed in could not be read: nothing else can be drawn. */
class ErrorBoundaryForBoot extends Component<{ children: ReactNode }, { error: unknown }> {
  state = { error: null as unknown };
  static getDerivedStateFromError(error: unknown) {
    return { error };
  }
  render() {
    return this.state.error ? <BootFailed error={this.state.error} /> : this.props.children;
  }
}
