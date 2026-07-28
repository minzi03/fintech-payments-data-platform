import { randomUUID } from "node:crypto";

import { Sdk } from "@fintech/portal-contracts";
import { createClient } from "@fintech/portal-contracts/client";
import { type NextRequest, NextResponse } from "next/server";

import { loadPortalWebRuntimeConfiguration } from "../../../config/server";

function loginFailure(request: NextRequest): NextResponse {
  return NextResponse.redirect(new URL("/login?failed=1", request.nextUrl.origin), 303);
}

function safeReturnPath(value: FormDataEntryValue | null): string | undefined {
  if (typeof value !== "string" || value.length === 0) {
    return undefined;
  }
  return value.length <= 512 ? value : undefined;
}

function providerRedirect(value: string | null): URL | null {
  if (!value) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const configuration = loadPortalWebRuntimeConfiguration();
    const client = createClient({
      baseUrl: configuration.apiInternalUrl,
      credentials: "include",
      responseStyle: "fields",
      throwOnError: false,
    });
    const sdk = new Sdk({ client });
    const form = await request.formData();
    const returnTo = safeReturnPath(form.get("return_to"));
    const headers: Record<string, string> = {
      Origin: configuration.publicOrigin,
      "X-Correlation-ID": randomUUID(),
    };
    const incomingCookies = request.headers.get("cookie");
    if (incomingCookies) {
      headers.Cookie = incomingCookies;
    }

    const contextResult = await sdk.getLoginContext({
      headers,
      query: { return_to: returnTo },
      signal: AbortSignal.timeout(5_000),
    });
    if (!contextResult.data) {
      return loginFailure(request);
    }
    const loginResult = await sdk.startLogin({
      body: {
        intent_token: contextResult.data.intent_token,
        return_to: contextResult.data.return_to,
      },
      headers,
      redirect: "manual",
      signal: AbortSignal.timeout(5_000),
    });
    const location = providerRedirect(loginResult.response?.headers.get("location") ?? null);
    if (loginResult.response?.status !== 303 || !location) {
      return loginFailure(request);
    }

    const response = NextResponse.redirect(location, 303);
    const bindingCookie = loginResult.response.headers.get("set-cookie");
    if (bindingCookie) {
      response.headers.append("set-cookie", bindingCookie);
    }
    return response;
  } catch {
    return loginFailure(request);
  }
}
