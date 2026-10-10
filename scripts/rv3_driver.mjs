// rv-3 证据采集的浏览器驱动：用真实 Playwright 打开 `kc console` 服务的
// Backlog 页面，按 plan 逐步操作并发控制条（UI 控件 → 真实 API → DB → 刷新页面
// fresh 读回），每步截图并把观测到的文案与真实 API 负载写入 summary JSONL。
//
// 只负责浏览器侧动作；fixture / console 启停与最终断言在 Python 侧（rv3_console.py）。
// 严格经页面控件写入（fill + 保存 / 恢复继承），不直接调 API 冒充页面通过。

import { createRequire } from "node:module";
import { appendFileSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const require = createRequire("/Users/zata/code/keda/.iar-worktrees/issue-266/package.json");
const { chromium } = require("/Users/zata/code/keda/.iar-worktrees/issue-266/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright");

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) {
    out[argv[i].replace(/^--/, "")] = argv[i + 1];
  }
  return out;
}

const opts = parseArgs(process.argv.slice(2));
const port = opts.port;
const repoId = opts.repo;
const plan = JSON.parse(readFileSync(resolve(opts.plan), "utf-8"));
const outDir = resolve(opts["out-dir"]);
const summaryPath = resolve(opts.summary);
writeFileSync(summaryPath, "", "utf-8");

const base = `http://127.0.0.1:${port}`;
const CONCURRENCY_TESTID = "backlog-concurrency";

// 捕获真实 API 负载：GET /backlog/settings 与 /backlog/autopilot 的响应体。
// PATCH /backlog/settings 单独同步记录（请求体 + 响应体），不依赖异步 response
// 事件缓冲——事件监听里 await response.json() 会晚于 capture() 落缓冲，负例曾因此
// 记成 _non_json。每一步开始前清空缓冲。
let apiLog = [];
let patchEvidence = null;
function resetApiLog() {
  apiLog = [];
  patchEvidence = null;
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });

// 预选仓库：静态导出下 pickInitialRepoId 走「记忆 id → 首个 enabled」，
// 只有一个仓库时本会自动选中；显式写入 lastRepoId 消除首屏竞态。
await context.addInitScript(
  ([id]) => {
    try {
      window.localStorage.setItem("iar.console.lastRepoId", id);
    } catch {
      /* localStorage 不可用时忽略，selection 仍会落到首个 enabled 仓库 */
    }
  },
  [repoId],
);

const page = await context.newPage();

page.on("response", async (response) => {
  const url = response.url();
  if (!url.includes("/api/v1/agent-runner/backlog/")) {
    return;
  }
  if (!/settings|autopilot/.test(url)) {
    return;
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = { _non_json: true };
  }
  apiLog.push({ url, status: response.status(), method: response.request().method(), payload });
});

async function waitForControlBar() {
  // 等控制条真实渲染出「并发」文本（骨架屏无此文本），再等 autopilot GET 落缓冲。
  await page.waitForFunction(
    (testid) => {
      const node = document.querySelector(`[data-testid="${testid}"]`);
      return node ? /并发\s*\d/.test(node.textContent || "") : false;
    },
    CONCURRENCY_TESTID,
    { timeout: 30000 },
  );
}

function concurrencyText() {
  // 只取控制条里的「并发 …」文本 span（外层 testid span 还包着输入框与按钮，
  // 直接读它会混进「保存 / 恢复继承」按钮文案）。
  return page.$eval(
    `[data-testid="${CONCURRENCY_TESTID}"] > span`,
    (node) => (node.textContent || "").replace(/\s+/g, " ").trim(),
  );
}

function latestAutopilot() {
  for (let i = apiLog.length - 1; i >= 0; i -= 1) {
    if (apiLog[i].url.includes("/autopilot")) {
      return apiLog[i];
    }
  }
  return null;
}
function latestSettingsGet() {
  for (let i = apiLog.length - 1; i >= 0; i -= 1) {
    const entry = apiLog[i];
    if (entry.url.includes("/settings") && entry.method === "GET") {
      return entry;
    }
  }
  return null;
}

async function capture(step) {
  const observed = await concurrencyText();
  const autopilot = latestAutopilot();
  const settingsGet = latestSettingsGet();
  const patch = patchEvidence;
  const shotPath = resolve(outDir, step.screenshot);
  // 分态主图：整条自动推进控制条（含开关 / 并发三态 / 输入框 / 保存 / 恢复继承 /
  // daemon 与自动合并状态），可读且与原型目标态图按状态配对。
  await page.locator(`[data-testid="backlog-autopilot"]`).screenshot({
    path: shotPath,
    animations: "disabled",
  });
  // 同时留一张整页图，便于人看上下文（不覆盖分态图）。
  await page.screenshot({
    path: resolve(outDir, step.screenshot.replace(/\.png$/, "-page.png")),
    fullPage: false,
    animations: "disabled",
  });
  const record = {
    step: step.name,
    expect_text: step.expect_text,
    observed_text: observed,
    text_match: observed === step.expect_text,
    expect_source: step.expect_source,
    autopilot: autopilot ? autopilot.payload : null,
    settings_get: settingsGet ? settingsGet.payload : null,
    settings_patch: patch,
    screenshot: step.screenshot,
  };
  appendFileSync(summaryPath, JSON.stringify(record) + "\n", "utf-8");
  return record;
}

// response 监听里 await response.json() 是异步的：等本步的 fresh GET 落缓冲再取数，
// 否则 capture 会读到 null 或上一步的旧负载。
async function waitForBufferedResponses() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (latestAutopilot() && latestSettingsGet()) {
      return;
    }
    await new Promise((waited) => setTimeout(waited, 50));
  }
}

// PATCH 写入证据在动作处同步取齐（请求体＝页面控件经 typed API client 发出的真实负载；
// 响应体＝服务端写回后的 fresh 值），不依赖异步事件缓冲。
async function recordPatchResponse(response) {
  if (!response) {
    return;
  }
  const request = response.request();
  let requestBody = null;
  try {
    requestBody = request.postDataJSON();
  } catch {
    requestBody = { _non_json: true, raw: request.postData() };
  }
  let responseBody = null;
  try {
    responseBody = await response.json();
  } catch {
    responseBody = { _non_json: true };
  }
  patchEvidence = {
    url: request.url(),
    method: request.method(),
    status: response.status(),
    request_body: requestBody,
    response_body: responseBody,
  };
}

// 首屏：goto 页面并等控制条渲染。
if (plan[0].action !== "noop") {
  // 防御：plan 首步约定为 noop（纯展示初始态）。
}

for (const step of plan) {
  if (step.action === "noop") {
    resetApiLog();
    await page.goto(`${base}/app/backlog`, { waitUntil: "domcontentloaded" });
    await waitForControlBar();
  } else if (step.action === "goto") {
    resetApiLog();
    await page.goto(`${base}/app/backlog`, { waitUntil: "domcontentloaded" });
    await waitForControlBar();
  } else if (step.action === "set_save") {
    resetApiLog();
    await page.fill(`[data-testid="backlog-concurrency-input"]`, String(step.value));
    const patchWait = page
      .waitForResponse(
        (r) => r.url().includes("/settings") && r.request().method() === "PATCH",
        { timeout: 20000 },
      )
      .catch(() => null);
    await page.click(`[data-testid="backlog-concurrency-save"]`);
    const patchResp = await patchWait;
    await recordPatchResponse(patchResp);
    // fresh 读回：整页刷新，丢弃前端状态，重新 GET。
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForControlBar();
  } else if (step.action === "restore") {
    resetApiLog();
    const patchWait = page
      .waitForResponse(
        (r) => r.url().includes("/settings") && r.request().method() === "PATCH",
        { timeout: 20000 },
      )
      .catch(() => null);
    await page.click(`[data-testid="backlog-concurrency-restore"]`);
    const patchResp = await patchWait;
    await recordPatchResponse(patchResp);
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForControlBar();
  } else {
    throw new Error(`unknown action: ${step.action}`);
  }
  await waitForBufferedResponses();
  await capture(step);
}

await browser.close();
