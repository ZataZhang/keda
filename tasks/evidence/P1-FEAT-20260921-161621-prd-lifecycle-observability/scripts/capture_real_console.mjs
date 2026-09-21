/**
 * rv-1 / rv-2 真实入口证据采集：驱动真实 Chromium 访问真实 IAR console
 * （uvicorn 提供静态前端 + 真实 FastAPI + 真实 SQLite）。
 *
 * 只 mock 会话守卫（/api/auth/me），其余请求全部打到真实后端；因此本脚本
 * 确实穿过 browser -> Next.js 静态页 -> /api/v1 -> FastAPI -> core 聚合 ->
 * SQLite -> fresh 渲染 的完整链路。
 *
 * 用法：node capture_real_console.mjs <baseUrl> <outDir>
 */

import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const requireFromE2E = createRequire(
  resolve(here, "../../../../tests/playwright-e2e/package.json"),
);
const { chromium } = requireFromE2E("playwright");

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8899";
const outDir = process.argv[3] ?? resolve(here, "..");

const PRD_PATH = "tasks/pending/P1-FEAT-20260921-161621-demo-lifecycle.md";

async function saveJson(name, payload) {
  await mkdir(outDir, { recursive: true });
  await writeFile(resolve(outDir, name), JSON.stringify(payload, null, 2) + "\n", "utf-8");
}

function encodePrdPath(prdPath) {
  return Buffer.from(prdPath, "utf-8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  // 会话守卫是纯客户端的：mock 一次 /api/auth/me 即可免登录，其余请求真实。
  await context.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "e2e",
        display_name: "E2E Reviewer",
        email: "e2e@example.com",
      }),
    }),
  );
  const page = await context.newPage();

  // ── rv-1: Roadmap 详情“执行过程” ─────────────────────────────────────────
  await page.goto(`${baseUrl}/app/roadmap`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /视图：/ }).click();
  await page.getByRole("menuitemradio", { name: "列表" }).click();
  await page.getByRole("button", { name: "查看原文" }).first().click();
  await page.getByTestId("prd-detail").waitFor();
  await page.getByTestId("prd-detail-tab-lifecycle").click();
  await page.getByTestId("prd-lifecycle-view").waitFor();
  await page.screenshot({
    path: resolve(outDir, "rv-1-roadmap-lifecycle.png"),
    fullPage: true,
  });

  // 真实 API fresh read：与页面同源的 HTTP 响应
  const detailResponse = await context.request.get(
    `${baseUrl}/api/v1/agent-runner/roadmap/prds/${encodePrdPath(PRD_PATH)}/lifecycle?repo_id=keda-main`,
  );
  await saveJson("rv-1-lifecycle-detail.json", await detailResponse.json());

  // 打开一个事件抽屉，验证时间/Agent/原因可见（等动画结束再截图）
  await page.getByTestId("prd-lifecycle-event-attempt").first().click();
  await page.getByTestId("prd-lifecycle-event-sheet").waitFor();
  // 抽屉必须显示关联 run id 与 Agent，作为“可从失败事件定位到 attempt/Agent/原因”的证据。
  await page.getByTestId("prd-lifecycle-event-sheet").getByText("keda-main#161").waitFor();
  await page.getByTestId("prd-lifecycle-event-sheet").getByText("codex").first().waitFor();
  await page.waitForTimeout(700);
  await page.screenshot({
    path: resolve(outDir, "rv-1b-roadmap-event-drawer.png"),
  });

  // ── rv-2: Stats PRD 端到端统计 ───────────────────────────────────────────
  await page.goto(`${baseUrl}/app/stats`, { waitUntil: "networkidle" });
  await page.getByTestId("stats-prd-lifecycle").waitFor();
  await page.screenshot({ path: resolve(outDir, "rv-2-prd-stats.png"), fullPage: true });

  const statsResponse = await context.request.get(
    `${baseUrl}/api/v1/agent-runner/console/stats/prd-lifecycle?repo_id=keda-main&days=30`,
  );
  await saveJson("rv-2-stats-response.json", await statsResponse.json());

  // ── 窄屏：Stats 生命周期卡片在窄屏下无横向溢出、指标与明细仍可读 ────────────
  // （Roadmap 详情在窄屏是主从布局的隐藏面板，不适合作为窄屏证据；原型侧另有
  //   Roadmap 窄屏检查。）
  const narrowPage = await context.newPage();
  await narrowPage.setViewportSize({ width: 390, height: 844 });
  await narrowPage.goto(`${baseUrl}/app/stats`, { waitUntil: "networkidle" });
  await narrowPage.getByTestId("stats-prd-lifecycle").waitFor();
  const overflow = await narrowPage.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  if (overflow > 2) {
    throw new Error(`narrow viewport has horizontal overflow: ${overflow}px`);
  }
  await narrowPage.screenshot({
    path: resolve(outDir, "rv-2b-narrow-prd-stats.png"),
    fullPage: true,
  });

  await browser.close();
  console.log("captured rv-1 / rv-2 evidence into", outDir);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
