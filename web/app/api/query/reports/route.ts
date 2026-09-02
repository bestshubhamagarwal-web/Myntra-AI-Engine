import { NextRequest } from "next/server";

import { proxyQuery } from "@/lib/query-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";
export const maxDuration = 60;

export async function GET(req: NextRequest) {
  return proxyQuery(req, ["reports"]);
}
