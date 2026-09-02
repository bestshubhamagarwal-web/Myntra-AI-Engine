import { NextRequest, NextResponse } from "next/server";

import { resolveBackendBase } from "@/lib/backend";

export function pathSegments(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.map((part) => String(part).trim()).filter(Boolean);
  }
  if (typeof raw === "string" && raw.trim()) {
    return raw.split("/").map((part) => part.trim()).filter(Boolean);
  }
  return [];
}

export async function proxyQuery(req: NextRequest, pathParts: string[]): Promise<NextResponse> {
  const resolved = resolveBackendBase();
  if (resolved.error) {
    return NextResponse.json({ detail: resolved.error }, { status: 503 });
  }

  const suffix = pathParts.join("/");
  if (!suffix) {
    return NextResponse.json(
      { detail: "Missing Query API path. Use /api/query/metrics/overview (or /health)." },
      { status: 400 },
    );
  }

  const target = new URL(`${resolved.url}/${suffix}`);
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
    "Query API unreachable. On the dashboard Vercel project set API_BASE_URL to the FastAPI origin (https://<api-project>.vercel.app).";
  const attempts = req.method === "GET" || req.method === "HEAD" ? 3 : 1;
  const timeoutMs = req.method === "POST" ? 110_000 : 20_000;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const upstream = await fetch(target, { ...init, signal: controller.signal });
      const payload = await upstream.arrayBuffer();
      const raw = new TextDecoder().decode(payload);
      if (upstream.status === 502 && /application failed to respond/i.test(raw)) {
        return NextResponse.json(
          {
            detail:
              "Query API is not reachable (502). Set API_BASE_URL to https://<api-project>.vercel.app (the FastAPI Vercel project, not Neon/Postgres). Confirm /health returns store=postgres.",
          },
          { status: 502 },
        );
      }
      if (
        upstream.status === 404 &&
        !/^\s*[{[]/.test(raw) &&
        !/application\/json/i.test(upstream.headers.get("content-type") || "")
      ) {
        return NextResponse.json(
          {
            detail: `Query API 404 at ${target.origin}${target.pathname}. Set the dashboard project's API_BASE_URL to the FastAPI Vercel origin (https://<api-project>.vercel.app) with no path, then Redeploy.`,
          },
          { status: 404 },
        );
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
        lastError = `Query API timed out talking to ${target.origin}. If the API project just deployed, retry.`;
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
