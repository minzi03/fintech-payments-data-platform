"use client";

import { useReportWebVitals } from "next/web-vitals";
import { useEffect } from "react";

import { recordPortalTelemetry } from "@/telemetry/events";

export function BrowserTelemetryObserver() {
  useReportWebVitals((metric) => {
    recordPortalTelemetry({
      event: "web_vital",
      outcome: "success",
      durationMs: metric.value,
    });
  });

  useEffect(() => {
    const recordError = () => {
      recordPortalTelemetry({ event: "ui_error", outcome: "failure" });
    };
    window.addEventListener("error", recordError);
    window.addEventListener("unhandledrejection", recordError);
    return () => {
      window.removeEventListener("error", recordError);
      window.removeEventListener("unhandledrejection", recordError);
    };
  }, []);

  return null;
}
