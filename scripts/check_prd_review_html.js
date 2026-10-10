// 人审清单 HTML 的真实浏览器呈现检查：控制台零 pageerror、首屏卡片、内嵌截图、翻页与结果生成。
const path = require('path');
const { chromium } = require('@playwright/test');

const EVID_DIR =
  '/Users/zata/code/keda/.iar-worktrees/issue-263/tasks/evidence/P2-FEAT-20261009-171037-agent-preset-performance-stats';
const HTML = path.join(EVID_DIR, 'human-review-checklist.html');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(String(err.message)));
  await page.goto(`file://${HTML}`);

  const firstCard = page.locator('.card.active h2');
  const firstCardText = await firstCard.textContent();
  console.log('FIRST_CARD', JSON.stringify(firstCardText));

  await page.getByRole('radio', { name: /同意：按 attempt 计算/ }).first().check();
  await page.click('#next');
  const secondCardText = await page.locator('.card.active h2').textContent();
  console.log('SECOND_CARD', JSON.stringify(secondCardText));
  const images = await page.locator('.card.active figure img').evaluateAll((els) =>
    els.map((el) => ({ src: el.getAttribute('src'), loaded: el.complete && el.naturalWidth > 0 })),
  );
  console.log('IMAGES', JSON.stringify(images));
  await page.screenshot({ path: path.join(EVID_DIR, 'rv-2-prd-review-checklist-page2.png'), fullPage: true });

  await page.getByRole('radio', { name: /已查看真实页面和窄屏结果/ }).first().check();
  await page.click('#next');
  const resultText = await page.locator('#result').textContent();
  console.log('RESULT_HAS_MARKDOWN', resultText.includes('人工审查结果'));
  console.log('RESULT_HAS_ANSWERS', resultText.includes('同意：按 attempt 计算') && resultText.includes('已查看真实页面'));
  console.log('PAGE_ERRORS', pageErrors.length);
  await browser.close();
  if (pageErrors.length > 0 || images.length !== 2 || images.some((i) => !i.loaded)) {
    process.exit(1);
  }
})().catch((err) => {
  console.error('ERR', err.message);
  process.exit(1);
});
