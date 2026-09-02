import { NextRequest, NextResponse } from "next/server";

import { pathSegments, proxyQuery } from "@/lib/query-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const dynamicParams = true;
export const fetchCache = "force-no-store";
export const maxDuration = 120;

type RouteContext = { params: Promise<{ path: string[] }> };

async function handle(req: NextRequest, ctx: RouteContext): Promise<NextResponse> {
  const { path } = await ctx.params;
  return proxyQuery(req, pathSegments(path));
}

export async function GET(req: NextRequest, ctx: RouteContext) {
  return handle(req, ctx);
}

export async function POST(req: NextRequest, ctx: RouteContext) {
  return handle(req, ctx);
}

export async function HEAD(req: NextRequest, ctx: RouteContext) {
  return handle(req, ctx);
}
