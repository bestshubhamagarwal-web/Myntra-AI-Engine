/**
 * Resolve the Query API origin for the Next.js server proxy.
 * Never expose this URL to the browser (no NEXT_PUBLIC_ required).
 *
 * On Vercel Services the web→api binding injects API_BASE_URL. Locally it is
 * http://127.0.0.1:8000 from web/.env.local.
 */

export function isVercelRuntime(): boolean {
  return process.env.VERCEL === "1" || Boolean(process.env.VERCEL_ENV);
}

export function isHostedVercel(): boolean {
  const env = (process.env.VERCEL_ENV || "").toLowerCase();
  return env === "production" || env === "preview";
}

function loopbackUrl(url: string): boolean {
  return /:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/i.test(url);
}

function postgresMisconfig(url: string): string | undefined {
  if (/\.railway\.internal(?::|\/|$)/i.test(url)) {
    return "API_BASE_URL uses a private network hostname. Vercel cannot reach *.railway.internal. Leave API_BASE_URL unset so the web→api service binding injects it, or set it to https://<api-project>.vercel.app if you split projects.";
  }
  if (/\.rlwy\.net(?::|\/|$)/i.test(url) || /\.proxy\.rlwy\.net(?::|\/|$)/i.test(url)) {
    return "API_BASE_URL points at Railway Postgres (*.rlwy.net), not the Query API. Do not paste a database URL. The binding injects the FastAPI origin.";
  }
  if (/dpg-[a-z0-9-]+/i.test(url) || /\.postgres\.render\.com/i.test(url)) {
    return "API_BASE_URL points at Render Postgres, not the Query API. Leave it unset for the Vercel service binding, or use https://<api-project>.vercel.app (no path) if you split projects.";
  }
  if (/\.neon\.tech(?::|\/|$)/i.test(url) || /\.neon\.build(?::|\/|$)/i.test(url)) {
    return "API_BASE_URL points at Neon Postgres (*.neon.tech), not the Query API. DATABASE_URL belongs on the FastAPI service. The dashboard proxy uses the web→api binding.";
  }
  if (/\.supabase\.(co|com)(?::|\/|$)/i.test(url)) {
    return "API_BASE_URL points at Supabase Postgres, not the Query API. Leave it unset for the Vercel service binding, or use https://<api-project>.vercel.app (no path) if you split projects.";
  }
  return undefined;
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

  const pgError = postgresMisconfig(url);
  if (pgError) {
    return { url, error: pgError };
  }

  if (isHostedVercel() && !raw) {
    return {
      url,
      error:
        "API_BASE_URL is missing. In a Vercel Services project the web service binding injects it at runtime — Redeploy after connecting the repo with Framework = Services. If you split projects, set API_BASE_URL to the FastAPI origin (https://<api-project>.vercel.app) and API_SHARED_SECRET, then Redeploy.",
    };
  }
  if (isHostedVercel() && loopbackUrl(url)) {
    return {
      url,
      error:
        "API_BASE_URL points at localhost on a hosted Vercel deployment. Remove the dashboard env override so the web→api binding can inject the FastAPI origin, then Redeploy.",
    };
  }

  try {
    const parsed = new URL(url);
    const publicHosted =
      parsed.hostname.endsWith("onrender.com") ||
      parsed.hostname.endsWith("up.railway.app") ||
      parsed.hostname.endsWith("vercel.app");
    if (publicHosted && parsed.protocol === "http:") {
      url = `https://${parsed.host}`;
    }
  } catch {
    return { url, error: `API_BASE_URL is not a valid URL: ${url}` };
  }
  return { url };
}
