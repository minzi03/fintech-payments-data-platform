import Link from "next/link";
import type { ReactNode } from "react";

import { portalConfig } from "@/api/config";

const navigation = [
  { href: "/", label: "Home" },
  { href: "/system-status", label: "System Status" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const links = portalConfig.developerNavigationEnabled
    ? [...navigation, { href: "/developer", label: "Developer" }]
    : navigation;

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
        <span className="environment-pill" aria-label={`Environment: ${portalConfig.environment}`}>
          {portalConfig.environment}
        </span>
      </header>
      <div className="workspace">
        <nav className="sidebar" aria-label="Foundation navigation">
          <p className="nav-section">Workspace</p>
          <ul>
            {links.map((link) => (
              <li key={link.href}>
                <Link href={link.href}>{link.label}</Link>
              </li>
            ))}
          </ul>
          <div className="sidebar-note">
            <p>Foundation only</p>
            <span>Operational controls arrive in later reviewed releases.</span>
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
