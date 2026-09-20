/**
 * Realistic validation for the lifecycle-agent-matrix console surfaces (PRD rv-3/4/6/7).
 *
 * 验证层级：**真实入口**。Settings / Roadmap / PRD 原文页三个界面都走真实后端与
 * 真实配置（`config.toml` / 仓库 `.iar.toml` / PRD 文件头部），不做任何 stub。
 *
 * 覆盖范围与边界（刻意不写盘）：本 spec 只做**只读交互**——打开两个 Tab、展开九行
 * 矩阵的下拉看候选值、断言回退顺序编辑器的可用性、打开仓库级矩阵抽屉与 PRD 覆盖抽屉。
 * 凡涉及"保存后文件真的变了"的断言，均由后端契约测试
 * (`tests/test_lifecycle_agents_console_api.py`, 以磁盘内容为事实源) 与手工真实入口
 * 证据 (`tasks/evidence/<stem>/rv-*.png`) 覆盖——e2e 跑在共享仓库上，写盘会污染
 * registry 里的真实 `config.toml` / `.iar.toml`。
 *
 * 依赖数据：需要一个**已启用**的受管理仓库（齿轮抽屉用）与至少一个 PRD（覆盖抽屉用）；
 * 缺失时对应用例 skip 而不是失败，这样在没有预置仓库的环境里也能稳定跑通。
 */

import type { Page } from '@playwright/test'

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
 * PRD 覆盖抽屉只呈递有 PRD 消费点的键：`planner` 的唯一消费点是 `iar ask`，
 * 既没有 Issue 也没有 PRD 上下文，PRD 级覆盖对它无效，因此后端不下发该行。
 */
const PRD_OVERRIDE_KEYS = LIFECYCLE_KEYS.filter((key) => key !== 'planner')

/** 打开 Settings 的「Agent 管理」区块并切到指定 Tab。 */
async function openAgentManagementTab(
  page: Page,
  tab: 'labels' | 'lifecycles'
): Promise<void> {
  await page.goto('/app/settings/')
  await expect(page.getByTestId('settings-agent-management')).toBeVisible()
  await page.getByTestId(`settings-agent-tab-${tab}`).click()
}

test.describe('生命周期 Agent 矩阵 (lifecycle-agent)', () => {
  test('Settings「Agent 管理」两个 Tab：默认停在标签设置，可切到生命周期设置', async ({
    page,
  }) => {
    await page.goto('/app/settings/')

    const agentManagement = page.getByTestId('settings-agent-management')
    await expect(agentManagement).toBeVisible()
    await expect(agentManagement.getByRole('heading', { name: 'Agent 管理' })).toBeVisible()

    // 默认页 = Tab ① 「Agent 标签设置」：每个已注册 agent 一行，含标签名/颜色/描述。
    await expect(page.getByTestId('agent-labels-editor')).toBeVisible()
    await expect(page.getByTestId('agent-label-name-codex')).toBeVisible()
    await expect(page.getByTestId('agent-label-color-codex')).toBeVisible()
    await expect(page.getByTestId('agent-label-description-codex')).toBeVisible()
    // 生命周期矩阵默认不在前台（Tab ② 未选中）。
    await expect(page.getByTestId('global-lifecycle-matrix-matrix')).toHaveCount(0)

    // 切到 Tab ② 「生命周期 Agent 设置」：九行矩阵 + 回退顺序卡片同时出现。
    await page.getByTestId('settings-agent-tab-lifecycles').click()
    await expect(page.getByTestId('global-lifecycle-matrix-matrix')).toBeVisible()
    await expect(page.getByTestId('fallback-order-editor')).toBeVisible()

    // 原有页面内容保持在区块下方。
    await expect(page.getByRole('heading', { name: '关于 iar 管理终端' })).toBeVisible()
  })

  test('全局矩阵九行齐全，下拉只给真实取值', async ({ page }) => {
    await openAgentManagementTab(page, 'lifecycles')

    for (const lifecycleKey of LIFECYCLE_KEYS) {
      await expect(page.getByTestId(`lifecycle-matrix-row-${lifecycleKey}`)).toBeVisible()
      await expect(page.getByTestId(`lifecycle-matrix-select-${lifecycleKey}`)).toBeVisible()
      await expect(page.getByTestId(`lifecycle-matrix-source-${lifecycleKey}`)).toBeVisible()
    }

    // 展开「校验」行：候选值只能是已注册 agent + 该阶段允许的 auto（不得有 executor，
    // 也不得有"未设置/跟随全局"之类伪选项）。
    await page.getByTestId('lifecycle-matrix-select-verifier').click()
    await expect(page.getByTestId('lifecycle-matrix-option-verifier-codex')).toBeVisible()
    await expect(page.getByTestId('lifecycle-matrix-option-verifier-claude')).toBeVisible()
    await expect(page.getByTestId('lifecycle-matrix-option-verifier-auto')).toBeVisible()
    await expect(page.getByTestId('lifecycle-matrix-option-verifier-executor')).toHaveCount(0)
  })

  test('修复/收尾行提供「跟随实现（executor）」，实现行不提供', async ({ page }) => {
    await openAgentManagementTab(page, 'lifecycles')

    await page.getByTestId('lifecycle-matrix-select-fix').click()
    await expect(page.getByTestId('lifecycle-matrix-option-fix-executor')).toBeVisible()
    await expect(page.getByTestId('lifecycle-matrix-option-fix-auto')).toHaveCount(0)
    await page.keyboard.press('Escape')

    await page.getByTestId('lifecycle-matrix-select-implementation').click()
    await expect(page.getByTestId('lifecycle-matrix-option-implementation-auto')).toBeVisible()
    await expect(
      page.getByTestId('lifecycle-matrix-option-implementation-executor')
    ).toHaveCount(0)
  })

  test('agent 回退顺序卡片：条目可排序/移除、最大切换次数可编辑', async ({ page }) => {
    await openAgentManagementTab(page, 'lifecycles')

    await expect(page.getByTestId('fallback-order-editor')).toBeVisible()
    await expect(page.getByTestId('fallback-order-max-switches')).toBeVisible()
    await expect(page.getByTestId('fallback-order-append')).toBeVisible()
    await expect(page.getByTestId('fallback-order-save')).toBeEnabled()

    const fallbackItems = page.locator('[data-testid^="fallback-order-item-"]')
    const orderBefore = await fallbackItems.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('data-testid') ?? '')
    )
    test.skip(orderBefore.length < 2, '当前回退链少于两项，跳过排序用例。')

    // 把第二项上移：只改本地状态，**不点保存**（e2e 跑在共享仓库上，写盘会污染真实 config.toml；
    // 落盘语义由 tests/test_lifecycle_agents_console_api.py 以磁盘内容为事实源覆盖）。
    const secondAgentName = orderBefore[1].replace('fallback-order-item-', '')
    await page.getByTestId(`fallback-order-up-${secondAgentName}`).click()
    await expect
      .poll(async () => (await fallbackItems.first().getAttribute('data-testid')) ?? '')
      .toBe(orderBefore[1])
  })

  test('Roadmap 受管理仓库行齿轮打开仓库级矩阵抽屉', async ({ page }) => {
    await page.goto('/app/roadmap/')
    // Roadmap 是 SPA：仓库列表与 PRD 列表是两次独立请求，等网络静默再计数，
    // 否则刚 goto 完就 count 会稳定拿到 0，把有仓库的情况误判成"没有受管理仓库"
    // 而跳过本用例（下方「Agent 覆盖」用例出于同一原因已经等待）。
    await page.waitForLoadState('networkidle')

    const gearButtons = page.locator('[data-testid^="repo-agent-gear-"]')
    const gearCount = await gearButtons.count()
    test.skip(gearCount === 0, '当前 registry 没有受管理仓库，跳过仓库级抽屉用例。')

    await gearButtons.first().click()
    const drawerMatrix = page.getByTestId('repo-lifecycle-matrix-matrix')
    await expect(drawerMatrix).toBeVisible()

    // 抽屉里同样九行；来源列必须标注出"继承自哪一层"。
    for (const lifecycleKey of LIFECYCLE_KEYS) {
      await expect(page.getByTestId(`lifecycle-matrix-row-${lifecycleKey}`)).toBeVisible()
      await expect(page.getByTestId(`lifecycle-matrix-source-${lifecycleKey}`)).toBeVisible()
    }
    // 未在本层声明的键不出现恢复入口（「跟随全局（删除本键）」只在已声明时出现）。
    await expect(page.getByTestId('lifecycle-matrix-restore-verifier')).toHaveCount(0)
  })

  test('PRD 原文页工具栏「Agent 覆盖」打开覆盖抽屉', async ({ page }) => {
    await page.goto('/app/roadmap/')
    // Roadmap 是 SPA：仓库列表与 PRD 列表是两次独立请求，等网络静默再计数，
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
    // 未勾选的行仍显示"当前生效值"，但下拉被禁用（未勾选 = 沿用仓库/全局层）。
    await expect(page.getByTestId('prd-agent-override-select-verifier')).toBeDisabled()
    // 勾选「校验」后该行下拉变为可编辑，代表声明了本 PRD 的覆盖。
    await page.getByTestId('prd-agent-override-toggle-verifier').click()
    await expect(page.getByTestId('prd-agent-override-select-verifier')).toBeEnabled()
    await expect(page.getByTestId('prd-agent-override-save')).toBeVisible()
  })
})
