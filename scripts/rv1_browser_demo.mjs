import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const playwrightModule =
  process.env.RV_PLAYWRIGHT_MODULE ??
  "/private/tmp/issue416-playwright/node_modules/playwright/index.mjs";
const playwright = await import(playwrightModule);
const browserEngine = process.env.RV_BROWSER_ENGINE ?? "chromium";
const browserType = playwright[browserEngine];
const [repositoryUrl, eventsPath, signalPath, evidenceDirectory, videoDirectory] =
  process.argv.slice(2);

if (!repositoryUrl || !eventsPath || !signalPath || !evidenceDirectory || !videoDirectory) {
  throw new Error("Expected repository URL, event log, signal, evidence directory, and video directory.");
}

if (!browserType) {
  throw new Error(`Unsupported Playwright browser engine: ${browserEngine}`);
}
await mkdir(evidenceDirectory, { recursive: true });
await mkdir(videoDirectory, { recursive: true });
const browser = await browserType.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  recordVideo: { dir: videoDirectory, size: { width: 1440, height: 1000 } },
});
const page = await context.newPage();
const pageVideo = page.video();
console.log(`BROWSER_ENGINE=${browserEngine}`);
let processedEventCount = 0;
const deadline = Date.now() + 180_000;

function getDemoPage(stage, issueNumber, pullUrl) {
  if (stage === "labels") {
    return {
      url: `${repositoryUrl}/labels`,
      caption: "kc init 完成，Gitea 仓库标签已同步",
    };
  }
  if (stage === "issue") {
    return {
      url: `${repositoryUrl}/issues/${issueNumber}`,
      caption: "kc issue create 创建了带就绪标签的 Issue",
    };
  }
  if (stage === "wip") {
    return {
      url: pullUrl,
      caption: "kc run 创建了 WIP 草稿 PR；执行器使用确定性替身",
    };
  }
  if (stage === "merged") {
    return {
      url: pullUrl,
      caption: "检查通过后 PR 已合并，WIP 前缀已移除，Issue 已关闭",
    };
  }
  throw new Error(`Unexpected browser demo stage: ${stage}`);
}

async function captureStage(event) {
  const demoPage = getDemoPage(event.stage, event.issue_number, event.pull_url);
  await page.goto(demoPage.url, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator("body").waitFor({ state: "visible", timeout: 15_000 });
  await page.waitForTimeout(1200);
  const bodyText = await page.locator("body").innerText();
  if (bodyText.includes("Page not found") || bodyText.includes("Internal Server Error")) {
    throw new Error(`Gitea rendered an error page for ${event.stage}.`);
  }
  if (event.stage === "wip" && !bodyText.includes("WIP: ")) {
    throw new Error("The real Gitea page did not render the WIP PR title.");
  }
  if (event.stage === "merged" && !/merged|已合并/i.test(bodyText)) {
    throw new Error("The real Gitea page did not render the merged PR state.");
  }

  if (event.stage === "wip") {
    const mergeControls = await page.locator("button, a[role='button']").evaluateAll((elements) =>
      elements
        .map((element) => ({
          label: (element.innerText || element.getAttribute("aria-label") || "").trim(),
          disabled:
            element.hasAttribute("disabled") || element.getAttribute("aria-disabled") === "true",
        }))
        .filter((element) => /merge/i.test(element.label)),
    );
    const screenshotPath = path.join(evidenceDirectory, "rv-1-gitea-pr-wip.png");
    await page.screenshot({ path: screenshotPath, fullPage: false });
    console.log(
      `WIP_PAGE=title_visible=true merge_controls=${JSON.stringify(mergeControls)} screenshot=${path.basename(screenshotPath)}`,
    );
  }
  if (event.stage === "merged") {
    const screenshotPath = path.join(evidenceDirectory, "rv-1-gitea-pr-merged.png");
    await page.screenshot({ path: screenshotPath, fullPage: false });
    console.log(`MERGED_PAGE=merged_visible=true screenshot=${path.basename(screenshotPath)}`);
  }

  await page.evaluate((captionText) => {
    document.getElementById("rv-evidence-caption")?.remove();
    const captionElement = document.createElement("div");
    captionElement.id = "rv-evidence-caption";
    captionElement.textContent = captionText;
    captionElement.setAttribute("aria-label", "演示字幕");
    captionElement.style.cssText = [
      "position:fixed",
      "left:0",
      "right:0",
      "bottom:0",
      "z-index:2147483647",
      "padding:20px 32px",
      "background:rgba(15,23,42,.92)",
      "color:#fff",
      "font:600 25px/1.4 -apple-system,BlinkMacSystemFont,sans-serif",
      "text-align:center",
      "box-shadow:0 -3px 20px rgba(0,0,0,.32)",
    ].join(";");
    document.body.appendChild(captionElement);
  }, demoPage.caption);
  await page.waitForTimeout(3500);

  if (event.stage === "wip") {
    await writeFile(signalPath, "wip-captured\n", { encoding: "utf-8" });
    console.log("WIP_CAPTURE_READY=true");
  }
}

try {
  let completed = false;
  while (Date.now() < deadline) {
    const eventLines = (await readFile(eventsPath, "utf-8").catch(() => ""))
      .split(/\r?\n/)
      .filter(Boolean);
    while (processedEventCount < eventLines.length) {
      const event = JSON.parse(eventLines[processedEventCount]);
      processedEventCount += 1;
      await captureStage(event);
      if (event.stage === "merged") {
        completed = true;
        break;
      }
    }
    if (completed) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!completed) {
    throw new Error("Timed out waiting for the full-loop browser events.");
  }
} finally {
  await context.close();
  await browser.close();
  if (pageVideo) {
    const recordedVideoPath = await pageVideo.path();
    console.log(`BROWSER_VIDEO_WEBM=${recordedVideoPath}`);
  }
}
