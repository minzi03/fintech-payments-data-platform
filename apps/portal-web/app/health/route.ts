import { NextResponse } from "next/server";

import { portalConfig } from "@/api/config";

export function GET() {
  return NextResponse.json(
    {
      service: "portal-web",
      status: "UP",
      version: portalConfig.webVersion,
    },
    {
      headers: {
        "Cache-Control": "no-store",
      },
    },
  );
}
