import type { Metadata } from "next";

import { SystemStatusPanel } from "@/features/system/system-status-panel";

export const metadata: Metadata = { title: "System Status" };

export default function SystemStatusPage() {
  return (
    <div className="page-stack">
      <header className="page-header">
        <p className="eyebrow">Operational foundation</p>
        <h1>System Status</h1>
        <p>
          Live BFF process state, readiness, and only the adapters that are actually configured.
        </p>
      </header>
      <SystemStatusPanel />
    </div>
  );
}
