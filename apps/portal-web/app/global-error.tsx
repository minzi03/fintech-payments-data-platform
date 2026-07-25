"use client";

import { useEffect } from "react";

import { recordPortalTelemetry } from "@/telemetry/events";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    recordPortalTelemetry({ event: "ui_error", outcome: "failure" });
  }, [error]);

  return (
    <html lang="en">
      <body>
        <main className="global-error" role="alert" aria-live="assertive">
          <p className="eyebrow">Contained UI error</p>
          <h1>The Portal could not render this view.</h1>
          <p>No infrastructure command was issued. Retry the view or return to the landing page.</p>
          <button className="button" type="button" onClick={reset}>
            Retry view
          </button>
        </main>
      </body>
    </html>
  );
}
