import { NextRequest, NextResponse } from "next/server";

import { pathSegments, proxyQuery } from "@/lib/query-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const dynamicParams = true;
export const fetchCache = "force-no-store";
export const maxDuration = 120;

type Ctx = { params: Promise<{ path?: string[] | string }> | { path?: string[] | string } };

async function handle(req: NextRequest, ctx: Ctx): Promise<NextResponse> {
  const params = await ctx.params;
  return proxyQuery(req, pathSegments(params?.path));
}

export async function GET(req: NextRequest, ctx: Ctx) {
  return handle(req, ctx);
}

export async function POST(req: NextRequest, ctx: Ctx) {
  return handle(req, ctx);
}

export async function HEAD(req: NextRequest, ctx: Ctx) {
  return handle(req, ctx);
}
