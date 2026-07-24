import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { BrowserTelemetryObserver } from "@/telemetry/browser-observer";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: {
    default: "Fintech Data Platform",
    template: "%s · Fintech Data Platform",
  },
  description: "Enterprise Data Platform Portal control-plane foundation.",
  robots: {
    index: false,
    follow: false,
  },
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <BrowserTelemetryObserver />
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
