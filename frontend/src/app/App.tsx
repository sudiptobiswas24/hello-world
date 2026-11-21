import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, type ReactNode } from "react";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router";

import { ApiError } from "../api/client";
import { AccessProvider } from "../auth/me";
import { ErrorPanel } from "../shell/ErrorPanel";
import { RouteError } from "../shell/RouteError";
import { Shell } from "../shell/Shell";
import { ToastProvider } from "../shell/Toasts";
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
            module.screens.flatMap((screen) => {
              const { detail, edit } = screen;
              return [
                { path: `${module.key}/${screen.path}`, lazy: async () => ({ Component: (await screen.load()).default }) },
                ...(detail
                  ? [{
                    path: `${module.key}/${screen.path}/:id`,
                    // A record's number, or "new" where the screen makes one. Anything else hung on
                    // "Opening..." with nothing asked: /production/work-orders/new, where runs come
                    // only from the plan, read "new" as a record and never fetched it.
                    loader: ({ params }: { params: { id?: string } }) => {
                      const id = params.id ?? "";
                      if (!/^\d+$/.test(id) && !(id === "new" && screen.create)) throw new Response("", { status: 404 });
                      return null;
                    },
                    lazy: async () => ({ Component: (await detail()).default }),
                  }]
                  : []),
                ...(edit
                  ? [{ path: `${module.key}/${screen.path}/:id/edit`, lazy: async () => ({ Component: (await edit()).default }) }]
                  : []),
              ];
            }),
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
      <ToastProvider>
        <Boot />
      </ToastProvider>
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
