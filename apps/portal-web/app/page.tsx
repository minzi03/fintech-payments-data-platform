import { ApiOverview } from "@/features/system/api-overview";

export default function HomePage() {
  return (
    <div className="page-stack">
      <section className="hero">
        <p className="eyebrow">PR-PORTAL-001 · Enterprise control plane</p>
        <h1>A secure foundation for the platform workspace.</h1>
        <p className="hero-copy">
          This release establishes the browser, BFF, contract, health, and observability boundaries.
          Operational data-platform capabilities are not enabled yet.
        </p>
        <div className="scope-row" aria-label="Foundation capabilities">
          <span>Versioned API</span>
          <span>Generated client</span>
          <span>Truthful health</span>
          <span>No infrastructure credentials</span>
        </div>
      </section>
      <ApiOverview />
      <section className="surface boundary-card" aria-labelledby="boundary-title">
        <div>
          <p className="eyebrow">Control-plane contract</p>
          <h2 id="boundary-title">One guarded path to platform services</h2>
        </div>
        <ol className="boundary-flow">
          <li>
            <span>01</span>
            Browser
          </li>
          <li>
            <span>02</span>
            Portal Web
          </li>
          <li>
            <span>03</span>
            Portal API
          </li>
          <li>
            <span>04</span>
            Versioned adapters
          </li>
        </ol>
        <p>
          The browser never receives PostgreSQL, Kafka, Airflow, or MinIO credentials and never
          connects to those services directly.
        </p>
      </section>
    </div>
  );
}
