import type { ReactNode } from "react";

import { portalConfig } from "@/api/config";
import { AuthorizedNavigation } from "@/features/auth/authorized-navigation";
import { SessionControls } from "@/features/auth/session-controls";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="topbar">
        <div className="brand-lockup">
          <span aria-hidden="true" className="brand-mark">
            FP
          </span>
          <div>
            <p className="brand-eyebrow">Control plane foundation</p>
            <p className="brand-name">Fintech Data Platform</p>
          </div>
        </div>
        <div className="topbar-actions">
          <span
            className="environment-pill"
            aria-label={`Environment: ${portalConfig.environment}`}
          >
            {portalConfig.environment}
          </span>
          <SessionControls />
        </div>
      </header>
      <div className="workspace">
        <nav className="sidebar" aria-label="Foundation navigation">
          <p className="nav-section">Workspace</p>
          <AuthorizedNavigation includeDeveloper={portalConfig.developerNavigationEnabled} />
          <div className="sidebar-note">
            <p>Server-authorized</p>
            <span>Capabilities are re-evaluated by the Portal API for every environment.</span>
          </div>
        </nav>
        <main id="main-content" className="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
      <footer className="footer">
        <span>Portal Web {portalConfig.webVersion}</span>
        <span>Build {portalConfig.buildSha}</span>
      </footer>
    </>
  );
}
