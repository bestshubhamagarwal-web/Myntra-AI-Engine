/**
 * Resolve the Railway Query API origin for the Vercel/Next.js server proxy.
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
        "API_BASE_URL is missing or points at localhost. In Vercel → Settings → Environment Variables set API_BASE_URL to the Railway public HTTPS origin (https://<service>.up.railway.app) and API_SHARED_SECRET to the same value as Railway, then Redeploy.",
    };
  }
  if (/\.railway\.internal(?::|\/|$)/i.test(url)) {
    return {
      url,
      error:
        "API_BASE_URL uses Railway private networking. Vercel cannot reach *.railway.internal. Use the public https://<service>.up.railway.app URL.",
    };
  }
  try {
    const parsed = new URL(url);
    if (parsed.hostname.endsWith("up.railway.app") && parsed.protocol === "http:") {
      url = `https://${parsed.host}`;
    }
  } catch {
    return { url, error: `API_BASE_URL is not a valid URL: ${url}` };
  }
  return { url };
}
