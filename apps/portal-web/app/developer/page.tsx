import { notFound } from "next/navigation";

import { developmentRoutesEnabled } from "@/features/development/route-policy";

export const dynamic = "force-dynamic";

export default function DeveloperPage() {
  if (!developmentRoutesEnabled()) {
    notFound();
  }
  return (
    <div className="page-stack">
      <header className="page-header">
        <p className="eyebrow">Development only</p>
        <h1>Portal developer information</h1>
        <p>Contract and failure-boundary tools available only outside production.</p>
      </header>
      <section className="surface">
        <h2>Versioned API contract</h2>
        <p>
          The interactive API documentation is served by the BFF and reached through the Portal Web
          boundary.
        </p>
        <a className="button" href="/portal-api/docs" rel="noopener noreferrer" target="_blank">
          Open API documentation
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      </section>
    </div>
  );
}
