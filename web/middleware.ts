import { NextRequest, NextResponse } from "next/server";

export function middleware(request: NextRequest) {
  const secret = (process.env.API_SHARED_SECRET || "").trim();
  if (!secret) return NextResponse.next();
  if (request.headers.get("x-api-key") || request.headers.get("authorization")) {
    return NextResponse.next();
  }
  const headers = new Headers(request.headers);
  headers.set("x-api-key", secret);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: "/api/query/:path*",
};
