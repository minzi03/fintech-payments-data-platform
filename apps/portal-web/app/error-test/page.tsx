import { notFound } from "next/navigation";

import { ErrorTrigger } from "@/features/development/error-trigger";
import { developmentRoutesEnabled } from "@/features/development/route-policy";

export const dynamic = "force-dynamic";

export default function ErrorTestPage() {
  if (!developmentRoutesEnabled()) {
    notFound();
  }
  return (
    <div className="page-stack">
      <header className="page-header">
        <p className="eyebrow">Development diagnostics</p>
        <h1>Error boundary probe</h1>
        <p>This route is excluded from production behavior and navigation.</p>
      </header>
      <section className="surface">
        <h2>UI failure containment</h2>
        <p>Use this control to validate the sanitized global error boundary.</p>
        <ErrorTrigger />
      </section>
    </div>
  );
}
