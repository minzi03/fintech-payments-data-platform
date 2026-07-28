import { randomUUID } from "node:crypto";

import { Sdk, type SessionView } from "@fintech/portal-contracts";
import { createClient } from "@fintech/portal-contracts/client";
import { cookies } from "next/headers";

import { loadPortalWebRuntimeConfiguration } from "../../config/server";

export async function currentServerSession(): Promise<SessionView | null> {
  try {
    const cookieHeader = (await cookies()).toString();
    if (!cookieHeader) {
      return null;
    }
    const client = createClient({
      baseUrl: loadPortalWebRuntimeConfiguration().apiInternalUrl,
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
