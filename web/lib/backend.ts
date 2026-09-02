/**
 * Resolve the Query API origin for the Vercel/Next.js server proxy.
 * Never expose this URL to the browser (no NEXT_PUBLIC_ required).
 */

export function isVercelRuntime(): boolean {
  return process.env.VERCEL === "1" || Boolean(process.env.VERCEL_ENV);
}

function loopbackUrl(url: string): boolean {
  return /:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/i.test(url);
}

export function resolveBackendBase(): { url: string; error?: string } {
  const raw = (
    process.env.API_BASE_URL ||
    process.env.DISCOVERY_API_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    ""
  ).trim();
  const fallback = "http://127.0.0.1:8000";
  let url = (raw || fallback).replace(/\/$/, "");
  url = url.replace("://localhost", "://127.0.0.1").replace("://[::1]", "://127.0.0.1");

  if (isVercelRuntime() && (!raw || loopbackUrl(url))) {
    return {
      url,
      error:
        "API_BASE_URL is missing or points at localhost. In the dashboard Vercel project → Settings → Environment Variables set API_BASE_URL to the FastAPI Vercel origin (https://<api-project>.vercel.app) and API_SHARED_SECRET to the same value as the API project, then Redeploy.",
    };
  }
  if (/\.railway\.internal(?::|\/|$)/i.test(url)) {
    return {
      url,
      error:
        "API_BASE_URL uses a private network hostname. Vercel cannot reach *.railway.internal. Use the public https://<api-project>.vercel.app URL.",
    };
  }
  if (/\.rlwy\.net(?::|\/|$)/i.test(url) || /\.proxy\.rlwy\.net(?::|\/|$)/i.test(url)) {
    return {
      url,
      error:
        "API_BASE_URL points at Railway Postgres (*.rlwy.net), not the Query API. Use https://<api-project>.vercel.app (no path).",
    };
  }
  if (/dpg-[a-z0-9-]+/i.test(url) || /\.postgres\.render\.com/i.test(url)) {
    return {
      url,
      error:
        "API_BASE_URL points at Render Postgres, not the Query API. Use https://<api-project>.vercel.app (no path).",
    };
  }
  if (/\.neon\.tech(?::|\/|$)/i.test(url) || /\.neon\.build(?::|\/|$)/i.test(url)) {
    return {
      url,
      error:
        "API_BASE_URL points at Neon Postgres (*.neon.tech), not the Query API. Use https://<api-project>.vercel.app (no path).",
    };
  }
  if (/\.supabase\.(co|com)(?::|\/|$)/i.test(url)) {
    return {
      url,
      error:
        "API_BASE_URL points at Supabase Postgres, not the Query API. Use https://<api-project>.vercel.app (no path).",
    };
  }
  try {
    const parsed = new URL(url);
    const hosted =
      parsed.hostname.endsWith("onrender.com") ||
      parsed.hostname.endsWith("up.railway.app") ||
      parsed.hostname.endsWith("vercel.app");
    if (hosted && parsed.protocol === "http:") {
      url = `https://${parsed.host}`;
    }
  } catch {
    return { url, error: `API_BASE_URL is not a valid URL: ${url}` };
  }
  return { url };
}
