// 打开 rv5 live 测试在真实 Gitea 上留下的失败状态 PR 页面，确认失败检查名可见后截图。
const playwrightModule =
  process.env.RV_PLAYWRIGHT_MODULE ??
  "/private/tmp/issue416-playwright/node_modules/playwright/index.mjs";
const playwright = await import(playwrightModule);
const browserEngine = process.env.RV_BROWSER_ENGINE ?? "chromium";
const [giteaBaseUrl, screenshotPath] = process.argv.slice(2);
const failingCheckName = "rv-failing-check";

if (!giteaBaseUrl || !screenshotPath) {
  throw new Error("Expected the Gitea base URL and the screenshot output path.");
}

/** 以只读方式调用 Gitea API；令牌只放在请求头里，不输出。 */
async function fetchGiteaJson(apiPath) {
  const apiResponse = await fetch(`${giteaBaseUrl}/api/v1/${apiPath}`, {
    headers: { Authorization: `token ${process.env.RV_GITEA_TOKEN ?? ""}` },
  });
  if (!apiResponse.ok) {
    throw new Error(`Gitea API ${apiPath} returned HTTP ${apiResponse.status}.`);
  }
  return apiResponse.json();
}

const repositorySearch = await fetchGiteaJson("repos/search?q=rv-&sort=created&order=desc&limit=50");
const liveTestRepository = (repositorySearch.data ?? []).find((repository) =>
  /^rv-[0-9a-f]{12}$/.test(repository.name),
);
if (!liveTestRepository) {
  throw new Error("No repository created by the rv5 live status test was found.");
}
const pullRequests = await fetchGiteaJson(`repos/${liveTestRepository.full_name}/pulls?state=all`);
// 测试以草稿创建 PR，Gitea 上的标题带 `WIP: ` 前缀。
const failurePullRequest = pullRequests.find(
  (pull) => pull.title.replace(/^WIP: /, "") === "Status aggregation failure",
);
if (!failurePullRequest) {
  throw new Error("The rv5 failure-status Pull Request was not found.");
}
const commitStatus = await fetchGiteaJson(
  `repos/${liveTestRepository.full_name}/commits/${failurePullRequest.head.sha}/status`,
);
console.log(
  `API_STATUS=state=${commitStatus.state} contexts=${JSON.stringify(
    (commitStatus.statuses ?? []).map((status) => `${status.context}:${status.status}`),
  )}`,
);

const browser = await playwright[browserEngine].launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(failurePullRequest.html_url, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator("body").waitFor({ state: "visible", timeout: 15_000 });
  let failingCheckLocator = page.getByText(failingCheckName, { exact: false }).first();
  if (!(await failingCheckLocator.isVisible().catch(() => false))) {
    const showChecksToggle = page.getByText(/show all checks/i).first();
    if (await showChecksToggle.isVisible().catch(() => false)) {
      await showChecksToggle.click();
    }
    failingCheckLocator = page.getByText(failingCheckName, { exact: false }).first();
  }
  await failingCheckLocator.waitFor({ state: "visible", timeout: 15_000 });
  await failingCheckLocator.scrollIntoViewIfNeeded();
  await page.waitForTimeout(800);
  await page.screenshot({ path: screenshotPath, fullPage: false });
  console.log(
    `STATUS_PAGE=engine=${browserEngine} pr=${failurePullRequest.number} failing_check_visible=true screenshot=rv-5-gitea-status.png`,
  );
} finally {
  await browser.close();
}
