type PortalTelemetryDetail = Readonly<{
  event: "api_request" | "ui_error" | "web_vital";
  durationMs?: number;
  route?: string;
  outcome: "success" | "failure";
}>;

export function recordPortalTelemetry(detail: PortalTelemetryDetail): void {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent<PortalTelemetryDetail>("portal:telemetry", { detail }));
}
