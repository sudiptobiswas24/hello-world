import { isRouteErrorResponse, useRouteError } from "react-router";

import { ErrorPanel } from "./ErrorPanel";

/**
 * A screen that failed to load or to draw. The commonest cause after an
 * update is a browser still holding the old page, asking for script files
 * the update replaced: that says so and offers a reload, rather than
 * showing a broken screen.
 */
export function RouteError() {
  const error = useRouteError();
  const text = error instanceof Error ? error.message : "";
  if (/dynamically imported module|Importing a module script failed|Loading chunk/i.test(text)) {
    return (
      <div className="error-panel" role="alert">
        <h2>A new version is ready</h2>
        <p>The application was updated while this page was open.</p>
        <button type="button" onClick={() => window.location.reload()}>Reload</button>
      </div>
    );
  }
  if (isRouteErrorResponse(error) && error.status === 404) {
    return (
      <div className="error-panel" role="alert">
        <h2>No such screen</h2>
        <p>The address does not name a screen of this application.</p>
        <a href="/app/">Go to the start</a>
      </div>
    );
  }
  return <ErrorPanel error={error} />;
}
