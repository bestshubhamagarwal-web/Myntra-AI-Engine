import dns from "node:dns";
import { NextRequest, NextResponse } from "next/server";

import { resolveBackendBase } from "@/lib/backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";
export const maxDuration = 120;

dns.setDefaultResultOrder("ipv4first");

async function proxy(req: NextRequest, pathParts: string[]): Promise<NextResponse> {
  const resolved = resolveBackendBase();
  if (resolved.error) {
    return NextResponse.json({ detail: resolved.error }, { status: 503 });
  }

  const target = new URL(`${resolved.url}/${pathParts.join("/")}`);
  req.nextUrl.searchParams.forEach((value, key) => {
    target.searchParams.set(key, value);
  });

  const headers = new Headers();
  const incomingKey = req.headers.get("x-api-key") || req.headers.get("authorization");
  const envKey = (process.env.API_SHARED_SECRET || "").trim();
  if (incomingKey && incomingKey.toLowerCase().startsWith("bearer ")) {
    headers.set("Authorization", incomingKey);
  } else if (incomingKey) {
    headers.set("X-API-Key", incomingKey);
  } else if (envKey) {
    headers.set("X-API-Key", envKey);
  }
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  headers.set("Accept", req.headers.get("accept") || "application/json");
  headers.set("User-Agent", "myntra-discovery-web/0.6");

  const init: RequestInit = {
    method: req.method,
    headers,
    cache: "no-store",
    redirect: "follow",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  let lastError =
    "Query API unreachable. On Vercel set API_BASE_URL to the Railway public HTTPS URL.";
  const attempts = req.method === "GET" || req.method === "HEAD" ? 3 : 1;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20_000);
    try {
      const upstream = await fetch(target, { ...init, signal: controller.signal });
      const payload = await upstream.arrayBuffer();
      if (upstream.status === 502) {
        const raw = new TextDecoder().decode(payload);
        if (/application failed to respond/i.test(raw)) {
          return NextResponse.json(
            {
              detail:
                "Railway API is not reachable (502). Set the public-domain port to the same value as Variables → PORT (often 8080, not 443). The API must be the web service, not Postgres.",
            },
            { status: 502 },
          );
        }
      }
      const out = new Headers();
      const ct = upstream.headers.get("content-type");
      if (ct) out.set("content-type", ct);
      const disposition = upstream.headers.get("content-disposition");
      if (disposition) out.set("content-disposition", disposition);
      return new NextResponse(payload, { status: upstream.status, headers: out });
    } catch (error) {
      const name = error instanceof Error ? error.name : "";
      const message = error instanceof Error ? error.message : String(error);
      if (name === "AbortError" || name === "TimeoutError") {
        lastError = `Query API timed out talking to ${target.origin}. If Railway just deployed, retry.`;
        if (attempt === attempts - 1) {
          return NextResponse.json({ detail: lastError }, { status: 504 });
        }
      } else {
        lastError = `Query API unreachable at ${target.origin}: ${message}`;
      }
    } finally {
      clearTimeout(timer);
    }
  }
  return NextResponse.json({ detail: lastError }, { status: 502 });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function POST(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
