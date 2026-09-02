import { NextRequest } from "next/server";

import { proxyQuery } from "@/lib/query-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";
export const maxDuration = 120;

export async function POST(req: NextRequest) {
  return proxyQuery(req, ["copilot", "query"]);
}
