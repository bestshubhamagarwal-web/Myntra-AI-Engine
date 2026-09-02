import type { NextConfig } from "next";

const apiBase = (process.env.API_BASE_URL || "").trim().replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    if (!apiBase || /localhost|127\.0\.0\.1/i.test(apiBase)) {
      return [];
    }
    return [
      {
        source: "/api/query/:path*",
        destination: `${apiBase}/:path*`,
      },
    ];
  },
};

export default nextConfig;
