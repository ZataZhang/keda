// rv-2 呈递截图：从真实 kc console 静态分发的 /app/stats 采集桌面与窄屏证据。
const { chromium } = require('@playwright/test');

const OUT =
  '/Users/zata/code/keda/.iar-worktrees/issue-263/tasks/evidence/P2-FEAT-20261009-171037-agent-preset-performance-stats';

(async () => {
  const browser = await chromium.launch();

  const ctx = await browser.newContext({
    baseURL: 'http://127.0.0.1:8399',
    viewport: { width: 1440, height: 900 },
  });
  const page = await ctx.newPage();
  await page.goto('/app/stats/');
  const card = page.getByTestId('stats-agent-performance');
  await card.waitFor({ state: 'visible', timeout: 60000 });
  await page
    .getByTestId('stats-agent-performance-agent-table')
    .waitFor({ state: 'visible', timeout: 60000 });
  await card.screenshot({ path: `${OUT}/rv-2-stats-agent-performance.png` });
  await page.screenshot({
    path: `${OUT}/rv-2-stats-agent-performance-desktop-page.png`,
  });
  console.log('DESKTOP_OK');

  const nctx = await browser.newContext({
    baseURL: 'http://127.0.0.1:8399',
    viewport: { width: 375, height: 812 },
  });
  const npage = await nctx.newPage();
  await npage.goto('/app/stats/');
  const ncard = npage.getByTestId('stats-agent-performance');
  await ncard.waitFor({ state: 'visible', timeout: 60000 });
  // 375px 下导航未收起时内容盒宽度为 0，先收起再等内容出现。
  // 点击可能落在 hydration 完成前，重试直到按钮切换为「展开导航栏」。
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const collapsed = await npage
      .getByRole('button', { name: '展开导航栏' })
      .isVisible()
      .catch(() => false);
    if (collapsed) {
      break;
    }
    await npage.getByRole('button', { name: '收起导航栏' }).click().catch(() => {});
    await npage.waitForTimeout(2000);
  }
  await npage
    .getByRole('button', { name: '展开导航栏' })
    .waitFor({ state: 'visible', timeout: 30000 });
  await npage.waitForTimeout(400);
  await npage
    .getByTestId('stats-agent-performance-agent-table')
    .waitFor({ state: 'visible', timeout: 60000 });
  const dims = await npage
    .getByTestId('stats-agent-performance-preset-table')
    .evaluate((el) => ({
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
      overflowX: getComputedStyle(el).overflowX,
    }));
  console.log('NARROW_DIMS', JSON.stringify(dims));
  await ncard.screenshot({
    path: `${OUT}/rv-2-stats-agent-performance-narrow.png`,
  });
  console.log('NARROW_OK');

  await browser.close();
})().catch((err) => {
  console.error('ERR', err.message);
  process.exit(1);
});
