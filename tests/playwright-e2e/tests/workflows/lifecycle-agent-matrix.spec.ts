/**
 * Realistic validation for the PRD-level lifecycle agent override drawer (rv-4).
 *
 * 验证层级：**真实入口**。PRD 原文页走真实后端与真实 PRD 文件头部，不做任何 stub。
 *
 * 覆盖范围与边界（刻意不写盘）：本 spec 只做**只读交互**——从 Backlog 打开一份 PRD，
 * 点开工具栏「Agent 覆盖」抽屉，断言九行分组、来源与勾选后下拉可用。凡涉及"保存后
 * PRD 头部真的变了"的断言，均由后端契约测试
 * (`tests/test_lifecycle_agents_console_api.py`, 以磁盘内容为事实源) 与手工真实入口
 * 证据 (`tasks/evidence/<stem>/rv-*.png`) 覆盖——e2e 跑在共享仓库上，写盘会污染真实 PRD。
 *
 * 依赖数据：至少一个 PRD（覆盖抽屉用）；缺失时用例 skip 而不是失败，这样在没有预置
 * PRD 的环境里也能稳定跑通。
 *
 * 注：原先分散在 Settings「生命周期 Agent 设置」Tab、Backlog 仓库齿轮抽屉里的九行矩阵与
 * 回退顺序卡片，已合并进统一的「生命周期、模型与推理深度设置」页
 * (`/app/settings/lifecycle/`)，对应新入口的 e2e 见
 * `tests/playwright-e2e/tests/workflows/lifecycle-settings.spec.ts`。本文件仅保留仍然独立
 * 存在的 PRD 覆盖抽屉用例。
 */

import { expect, test } from '../../fixtures/session.fixture'

const LIFECYCLE_KEYS = [
  'implementation',
  'fix',
  'closeout',
  'verifier',
  'review',
  'supervisor',
  'planner',
  'content_generation',
  'deliberate',
] as const

/**
 * PRD 覆盖抽屉只呈递有 PRD 消费点的键：`planner` 的唯一消费点是 `kc ask`，
 * 既没有 Issue 也没有 PRD 上下文，PRD 级覆盖对它无效，因此后端不下发该行。
 */
const PRD_OVERRIDE_KEYS = LIFECYCLE_KEYS.filter((key) => key !== 'planner')

test.describe('PRD 生命周期 Agent 覆盖抽屉 (prd-agent-override)', () => {
  test('PRD 原文页工具栏「Agent 覆盖」打开覆盖抽屉', async ({ page }) => {
    await page.goto('/app/backlog/')
    // Backlog 是 SPA：仓库列表与 PRD 列表是两次独立请求，等网络静默再计数，
    // 否则刚 goto 完就 count 会稳定拿到 0（把有 PRD 的仓库误判成"没有 PRD"）。
    await page.waitForLoadState('networkidle')

    const prdOpenButtons = page.locator('[data-testid="prd-open-content"]')
    const prdCount = await prdOpenButtons.count()
    test.skip(prdCount === 0, '当前仓库没有可见 PRD，跳过 PRD 覆盖抽屉用例。')

    await prdOpenButtons.first().click()
    const overrideButton = page.getByTestId('prd-agent-override-open')
    await expect(overrideButton).toBeVisible()
    await overrideButton.click()

    await expect(page.getByTestId('prd-agent-override-list')).toBeVisible()
    for (const lifecycleKey of PRD_OVERRIDE_KEYS) {
      await expect(page.getByTestId(`prd-agent-override-row-${lifecycleKey}`)).toBeVisible()
      await expect(page.getByTestId(`prd-agent-override-toggle-${lifecycleKey}`)).toBeVisible()
    }
    // planner 没有 PRD 消费点，不出现在覆盖抽屉里。
    await expect(page.getByTestId('prd-agent-override-row-planner')).toHaveCount(0)

    // 覆盖抽屉沿用同一分组：有行的组标题出现，「独立入口」组（只有 planner）整组缺席。
    await expect(page.getByTestId('prd-agent-override-entry-pipeline')).toBeVisible()
    await expect(page.getByTestId('prd-agent-override-entry-discussion_content')).toBeVisible()
    await expect(page.getByTestId('prd-agent-override-entry-standalone')).toHaveCount(0)
    // 辩论与内容生成落在「讨论与内容生成」组，不混入实现流水线组。
    const discussionGroup = page.getByTestId('prd-agent-override-entry-discussion_content')
    await expect(discussionGroup.getByTestId('prd-agent-override-row-deliberate')).toBeVisible()
    await expect(
      discussionGroup.getByTestId('prd-agent-override-row-content_generation')
    ).toBeVisible()
    await expect(
      page
        .getByTestId('prd-agent-override-entry-pipeline')
        .getByTestId('prd-agent-override-row-deliberate')
    ).toHaveCount(0)

    // 未勾选的行仍显示"当前生效值"，但下拉被禁用（未勾选 = 沿用仓库/全局层）。
    await expect(page.getByTestId('prd-agent-override-select-verifier')).toBeDisabled()
    // 勾选「校验」后该行下拉变为可编辑，代表声明了本 PRD 的覆盖。
    await page.getByTestId('prd-agent-override-toggle-verifier').click()
    await expect(page.getByTestId('prd-agent-override-select-verifier')).toBeEnabled()
    await expect(page.getByTestId('prd-agent-override-save')).toBeVisible()
  })
})
