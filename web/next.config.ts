import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Do not rewrite /api/query to the FastAPI origin. App Router routes in
  // app/api/query inject X-API-Key from API_SHARED_SECRET (server-only).
};

export default nextConfig;
