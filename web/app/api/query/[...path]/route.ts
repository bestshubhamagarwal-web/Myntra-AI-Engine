import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

function backendBase(): string {
  return (process.env.API_BASE_URL || "http://127.0.0.1:8000")
    .replace(/\/$/, "")
    .replace("://localhost", "://127.0.0.1")
    .replace("://[::1]", "://127.0.0.1");
}

async function proxy(req: NextRequest, pathParts: string[]): Promise<NextResponse> {
  const target = new URL(`${backendBase()}/${pathParts.join("/")}`);
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

  const init: RequestInit = {
    method: req.method,
    headers,
    cache: "no-store",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  let lastError = "Query API unreachable. Start it with python -m src.cli serve.";
  const attempts = req.method === "GET" || req.method === "HEAD" ? 3 : 1;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 45_000);
    try {
      const upstream = await fetch(target, { ...init, signal: controller.signal });
      const payload = await upstream.arrayBuffer();
      const out = new Headers();
      const ct = upstream.headers.get("content-type");
      if (ct) out.set("content-type", ct);
      const disposition = upstream.headers.get("content-disposition");
      if (disposition) out.set("content-disposition", disposition);
      return new NextResponse(payload, { status: upstream.status, headers: out });
    } catch (error) {
      const name = error instanceof Error ? error.name : "";
      if (name === "AbortError" || name === "TimeoutError") {
        return NextResponse.json(
          { detail: "Query API timed out. Retry the question." },
          { status: 504 },
        );
      }
      lastError = "Query API unreachable. Start it with python -m src.cli serve.";
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
