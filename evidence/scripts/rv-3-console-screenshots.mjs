// rv-3：在真实管理终端里截取并行上限的显示，并走一遍"保存页面设置 → 恢复为配置文件值"。
// 用法：node rv-3-console-screenshots.mjs <console-base-url> <evidence-dir> <repo-id>
import { chromium } from "../../../../tests/playwright-e2e/node_modules/@playwright/test/index.mjs";

const [baseUrl, evidenceDir, repoId] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));

await page.goto(`${baseUrl}/app/dashboard/`, { waitUntil: "networkidle" });
await page.getByTestId("runner-status-bar").waitFor();
await page.screenshot({ path: `${evidenceDir}/rv-3-dashboard-status-bar.png`, clip: { x: 0, y: 0, width: 1440, height: 420 } });

await page.goto(`${baseUrl}/app/backlog/`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: repoId, exact: true }).click();
const concurrency = page.getByTestId("backlog-concurrency");
await concurrency.waitFor();
const before = await concurrency.innerText();
await concurrency.screenshot({ path: `${evidenceDir}/rv-3-backlog-config-source.png` });

await page.getByTestId("backlog-concurrency-input").fill("4");
await page.getByTestId("backlog-concurrency-save").click();
await page.getByText("并行上限 4（页面设置）").waitFor();
const saved = await concurrency.innerText();
await concurrency.screenshot({ path: `${evidenceDir}/rv-3-backlog-policy-source.png` });

await page.getByTestId("backlog-concurrency-restore").click();
await page.getByText("（配置文件）").waitFor();
const restored = await concurrency.innerText();
await concurrency.screenshot({ path: `${evidenceDir}/rv-3-backlog-restored.png` });

console.log(JSON.stringify({ before, saved, restored, pageErrors }, null, 2));
await browser.close();
