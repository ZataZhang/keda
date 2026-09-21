# Evidence branch — PRD 生命周期观测与执行分析

这个分支是一个 **orphan（无历史）分支**，只用于承载 PR #153 的**二进制/大体积证据**
（截图、录屏、真实 HTTP 响应 JSON、采集脚本）。它们被主仓库 `.gitignore` 排除
（`tasks/evidence/**` 只放行 `*.md`），因此不会进入 `main`。

- 关联 PR：https://github.com/ZataZhang/keda/pull/153
- 源分支：`prd-lifecycle-observability`（提交 `11a5bd8f`）
- 证据文件：`tasks/evidence/P1-FEAT-20260921-161621-prd-lifecycle-observability/`
  - `*.evidence-report.md` —— 含内联截图的证据报告（在本分支上可直接渲染）
  - `*.verification-plan.md` / `*.verifier-report.md` —— 验证计划与两轮独立复核结论
  - `rv-1-*.png` / `rv-1b-*.png` / `rv-2-*.png` / `rv-2b-*.png` —— 真实入口截图
  - `rv-*.json` / `rv-*.txt` —— 真实 HTTP fresh-read 响应与 pytest 输出
  - `e2e/` —— Playwright spec 的浏览器流程产物（HTTP 读端点用 fixture，与真实 harness 分目录）
  - `scripts/` —— 真实 console 证据采集 harness（隔离 HOME/IAR_CONFIG 启动真实 uvicorn）

## 如何查看

- 证据报告（推荐）：`tasks/evidence/P1-FEAT-20260921-161621-prd-lifecycle-observability/P1-FEAT-20260921-161621-prd-lifecycle-observability.evidence-report.md`
- 目录浏览：`tasks/evidence/P1-FEAT-20260921-161621-prd-lifecycle-observability/`

## 生命周期

**合并 PR #153 之后即可删除这个分支**——它不参与 `main` 的构建、测试或发布。

```bash
git push zata --delete evidence/P1-FEAT-20260921-161621-prd-lifecycle-observability
```
