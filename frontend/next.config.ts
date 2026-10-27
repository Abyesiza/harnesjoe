import type { NextConfig } from "next";
import { API_URL } from "./src/lib/server-env";

const config: NextConfig = {
  compress: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default config;
