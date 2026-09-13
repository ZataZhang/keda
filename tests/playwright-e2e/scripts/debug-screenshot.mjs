import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 脚本固定位于 tests/playwright-e2e/scripts/，仓库根目录在其上三级。
// 不能用写死的绝对路径：just copy 会把本脚本复制到任意路径的派生项目。
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(SCRIPT_DIR, "../../..");
const OUTPUT_DIR = resolve(SCRIPT_DIR, "../screenshots");

const SESSION = { user_id: "u_demo_001", display_name: "陈砚", email: "chen.yan@my-app.dev" };

// /dashboard 属于 admin 前端。just copy 为派生项目分配随机 admin 端口并写入
// .env.run-state，因此优先读取它；未用 just run 启动时退回模板默认 5173。
function resolveBaseUrl() {
  if (process.env.BASE_URL) return process.env.BASE_URL;
  try {
    const runStateText = readFileSync(resolve(REPO_ROOT, ".env.run-state"), "utf-8");
    const adminPortMatch = runStateText.match(/^FRONTEND_ADMIN_PORT=(\d+)/m);
    if (adminPortMatch) return `http://localhost:${adminPortMatch[1]}`;
  } catch {
    // 没有 .env.run-state（未用 just run 启动）时使用模板默认 admin 端口。
  }
  return "http://localhost:5173";
}

const BASE_URL = resolveBaseUrl();

async function main() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: "light",
  });
  await context.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const url = route.request().url();
    console.log(`[mock] ${url}`);
    if (url.endsWith("/api/auth/me")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SESSION) });
      return;
    }
    await route.fulfill({ status: 204, body: "" });
  });
  const page = await context.newPage();
  page.on("console", (msg) => console.log(`[browser:${msg.type()}] ${msg.text()}`));
  page.on("pageerror", (err) => console.log(`[pageerror] ${err.message}`));
  await page.goto(`${BASE_URL}/dashboard`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
  console.log("--- URL:", page.url());
  console.log("--- TITLE:", await page.title());
  const text = await page.evaluate(() => document.body.innerText.slice(0, 800));
  console.log("--- TEXT:", text);
  await page.screenshot({ path: join(OUTPUT_DIR, "dashboard-debug.png"), fullPage: true });
  await browser.close();
}
main().catch((err) => { console.error(err); process.exit(1); });
