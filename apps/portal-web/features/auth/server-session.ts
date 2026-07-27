import { randomUUID } from "node:crypto";

import { Sdk, type SessionView } from "@fintech/portal-contracts";
import { createClient } from "@fintech/portal-contracts/client";
import { cookies } from "next/headers";

function internalApiOrigin(): string {
  const value = process.env.PORTAL_API_INTERNAL_URL?.trim() || "http://127.0.0.1:8010";
  const url = new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("PORTAL_API_INTERNAL_URL must use HTTP or HTTPS.");
  }
  return url.toString();
}

export async function currentServerSession(): Promise<SessionView | null> {
  try {
    const cookieHeader = (await cookies()).toString();
    if (!cookieHeader) {
      return null;
    }
    const client = createClient({
      baseUrl: internalApiOrigin(),
      credentials: "include",
      responseStyle: "fields",
      throwOnError: false,
    });
    const sdk = new Sdk({ client });
    const result = await sdk.getSession({
      headers: {
        Cookie: cookieHeader,
        "X-Correlation-ID": randomUUID(),
      },
      signal: AbortSignal.timeout(5_000),
    });
    return result.data ?? null;
  } catch {
    return null;
  }
}
