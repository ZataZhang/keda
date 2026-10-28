// rv-4：daemon 用 --concurrency 运行时，Backlog 控件显示它实际在用的值与被覆盖的页面值。
// 用法：node rv-4-daemon-live-limit.mjs <console-base-url> <evidence-dir> <repo-id>
import { chromium } from "../../../../tests/playwright-e2e/node_modules/@playwright/test/index.mjs";

const [baseUrl, evidenceDir, repoId] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
await page.goto(`${baseUrl}/app/backlog/`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: repoId, exact: true }).click();
const label = page.getByTestId("backlog-concurrency-label");
await label.waitFor();
await page.getByText("daemon 命令行 --concurrency").waitFor();
await page.getByTestId("backlog-concurrency").screenshot({ path: `${evidenceDir}/rv-4-backlog-daemon-cli-override.png` });
console.log(JSON.stringify({ label: await label.innerText(), title: await label.getAttribute("title"), pageErrors }, null, 2));
await browser.close();
