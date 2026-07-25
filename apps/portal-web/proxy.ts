import { NextResponse } from "next/server";

export function proxy() {
  if (process.env.NODE_ENV === "production") {
    return new NextResponse("Not found", {
      status: 404,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": "text/plain; charset=utf-8",
      },
    });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/developer", "/error-test"],
};
