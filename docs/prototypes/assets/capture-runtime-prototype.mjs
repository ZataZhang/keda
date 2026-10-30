/**
 * 运行运行时看板、桌面悬浮窗和模型评测原型的截图采集。
 *
 * 前置条件：仓库根目录已启动 `uv run mkdocs serve -a 127.0.0.1:8000`。
 */
import { chromium } from "../../../tests/playwright-e2e/node_modules/@playwright/test/index.mjs";

const BASE_URL =
  "http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html";
const VIEWPORT = { width: 1440, height: 1000 };
const BROWSER = await chromium.launch({
  headless: true,
  args: ["--single-process", "--no-zygote"],
});
const PAGE = await BROWSER.newPage({
  viewport: VIEWPORT,
  deviceScaleFactor: 1,
});

/** 隐藏评审控件、交互提示和焦点光晕，不改变产品布局。 */
async function hidePrototypeChrome() {
  await PAGE.addStyleTag({
    content: `
      body.capture-product-only [data-prototype-chrome] { display: none !important; }
      body.capture-product-only .hotspot-target,
      body.capture-product-only .hotspot-target:hover,
      body.capture-product-only .hotspot-target:focus,
      body.capture-product-only .hotspot-target:focus-visible {
        outline: none !important;
        box-shadow: none !important;
      }
    `,
  });
  await PAGE.evaluate(() => {
    document.body.classList.add("capture-product-only");
    document.body.classList.remove("hotspots-on");
    document.activeElement?.blur();
  });
}

try {
  await PAGE.goto(`${BASE_URL}#dashboard`, { waitUntil: "networkidle" });
  await hidePrototypeChrome();
  await PAGE.screenshot({
    path: "docs/prototypes/assets/runtime-board-preview.png",
    fullPage: true,
  });

  await PAGE.goto(`${BASE_URL}#evaluations`, { waitUntil: "networkidle" });
  await hidePrototypeChrome();
  await PAGE.screenshot({
    path: "docs/prototypes/assets/runtime-evaluation-preview.png",
    fullPage: true,
  });

  await PAGE.goto(`${BASE_URL}#evaluation-baseline`, { waitUntil: "networkidle" });
  await hidePrototypeChrome();
  await PAGE.locator(".evaluation-baseline-results").screenshot({
    path: "docs/prototypes/assets/runtime-evaluation-baseline-preview.png",
  });
  const baselineEstimate = await PAGE.locator("#baseline-estimate").innerText();
  if (baselineEstimate !== "24") {
    throw new Error(`Expected 24 Agent executions at repeats=3, received ${baselineEstimate}`);
  }
  await PAGE.getByRole("button", { name: "校准候选" }).click();
  if ((await PAGE.locator("#baseline-estimate").innerText()) !== "72") {
    throw new Error("Calibration estimate should be 12 cases × 2 candidates × 3 repeats");
  }
  await PAGE.getByRole("button", { name: "真实 Agent" }).click();
  await PAGE.locator("#baseline-repeats").selectOption("4");
  await PAGE.locator("#baseline-holdout").check();
  if ((await PAGE.locator("#baseline-estimate").innerText()) !== "48") {
    throw new Error("Agent estimate should include 12 cases × 1 preset × 4 repeats");
  }
  await PAGE.locator("#baseline-repeats").selectOption("3");
  await PAGE.locator("#baseline-holdout").uncheck();
  await PAGE.goto(`${BASE_URL}#evaluations`, { waitUntil: "networkidle" });
  await hidePrototypeChrome();

  await PAGE.locator("[data-open-eval-run]").click();
  await PAGE.locator("[data-start-eval-run]").click();
  await hidePrototypeChrome();
  await PAGE.screenshot({
    path: "docs/prototypes/assets/runtime-consent-preview.png",
    fullPage: false,
  });

  await PAGE.locator("[data-close-payload-review]").click();
  await PAGE.goto(`${BASE_URL}#dashboard`, { waitUntil: "networkidle" });
  await PAGE.locator("[data-open-widget]").click();
  await hidePrototypeChrome();
  await PAGE.screenshot({
    path: "docs/prototypes/assets/runtime-widget-preview.png",
    fullPage: false,
  });

  await PAGE.setViewportSize({ width: 390, height: 844 });
  await PAGE.goto(`${BASE_URL}#evaluation-baseline`, { waitUntil: "networkidle" });
  await hidePrototypeChrome();
  const narrowWidth = await PAGE.evaluate(
    () => document.documentElement.scrollWidth,
  );
  if (narrowWidth > 390) {
    throw new Error(`Narrow evaluation-baseline page overflows: ${narrowWidth}px`);
  }
} finally {
  await BROWSER.close();
}
