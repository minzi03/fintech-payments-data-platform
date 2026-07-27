import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/auth/start/route";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("server-side login initiation", () => {
  it("keeps the login intent server-side and forwards only redirect and binding cookie", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            expires_at: "2026-07-27T10:05:00Z",
            intent_token: "server-only-intent",
            return_to: "/system-status",
            selected_provider: "local-development-provider",
          }),
          {
            headers: { "Content-Type": "application/json" },
            status: 200,
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(null, {
          headers: {
            Location: "http://idp.local/authorize?request=opaque",
            "Set-Cookie": "portal_browser_binding=protected; Path=/; HttpOnly; SameSite=lax",
          },
          status: 303,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/auth/start", {
      body: new URLSearchParams({ return_to: "/system-status" }),
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      method: "POST",
    });

    const response = await POST(request);

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("http://idp.local/authorize?request=opaque");
    expect(response.headers.get("set-cookie")).toContain("portal_browser_binding=protected");
    const contextRequest = fetchMock.mock.calls[0]?.[0] as Request;
    const loginRequest = fetchMock.mock.calls[1]?.[0] as Request;
    expect(new URL(contextRequest.url).pathname).toBe("/v1/auth/login-context");
    expect(new URL(contextRequest.url).searchParams.get("return_to")).toBe("/system-status");
    expect(await loginRequest.clone().json()).toEqual({
      intent_token: "server-only-intent",
      return_to: "/system-status",
    });
    expect(response.headers.get("location")).not.toContain("server-only-intent");
  });

  it("returns only a generic local failure redirect when initiation fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("provider detail")));
    const request = new NextRequest("http://localhost:3000/auth/start", {
      body: new URLSearchParams(),
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      method: "POST",
    });

    const response = await POST(request);

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("http://localhost:3000/login?failed=1");
    expect(response.headers.get("location")).not.toContain("provider");
  });
});
