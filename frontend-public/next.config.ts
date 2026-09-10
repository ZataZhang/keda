import type { NextConfig } from "next"

const backendUrl = process.env.BACKEND_URL || "http://localhost:8000"

const nextConfig: NextConfig = {
  devIndicators: false,
  allowedDevOrigins: ["127.0.0.1"],
  // 静态导出：产物由 `iar console` 内置的 FastAPI 直接托管（同源访问 /api）。
  // trailingSlash 让产物是 out/app/roadmap/index.html 这种目录形态，
  // 才能被 StaticFiles(html=True) 正确解析深层路由。
  output: "export",
  trailingSlash: true,
  async rewrites() {
    return [
      {
        // 仅 dev 模式生效的 /api 代理；生产（静态导出）与后端同源，不经过它。
        // keda 后端路由注册在 /api 前缀下（如 /api/auth/*、/api/v1/agent-runner/*），
        // 必须原样透传，不能像模板原状那样剥掉 /api。
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ]
  },
}

export default nextConfig
