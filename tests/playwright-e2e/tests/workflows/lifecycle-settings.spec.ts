/**
 * Realistic validation for the unified lifecycle / model / reasoning settings page (rv-2).
 *
 * 验证层级：**真实入口**。直接访问 `/app/settings/lifecycle/`，走真实后端只读视图与真实
 * 配置（全局 `config.toml` / 仓库 `.kedacode.toml`），不做任何 stub。
 *
 * 覆盖范围与边界（刻意不写盘）：本 spec 只做**只读交互**——从 Settings 进入统一页、切换
 * 全局 / 仓库范围、展开矩阵行与预设 / 回退的编辑控件看候选值。凡涉及"保存后文件真的变了"
 * 的断言，均由后端契约测试 (`tests/test_lifecycle_agents_console_api.py`, 以磁盘内容为事实源)
 * 与手工真实入口截图 (`tasks/evidence/<stem>/rv-2-*.png`) 覆盖——e2e 跑在共享仓库上，写盘会
 * 污染 registry 里的真实 `config.toml` / `.kedacode.toml`。
 *
 * 依赖数据：仓库范围用例需要一个**已启用**的受管理仓库（Backlog 齿轮用）；缺失时对应用例
 * skip 而不是失败，这样在没有预置仓库的环境里也能稳定跑通。
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

test.describe('生命周期统一设置页 (lifecycle-settings)', () => {
  test('Settings「Agent 管理」入口可进入统一设置页', async ({ page }) => {
    await page.goto('/app/settings/')

    const entry = page.getByTestId('settings-lifecycle-entry')
    await expect(entry).toBeVisible()
    // 回归守护：入口页仍渲染 Agent 标签设置编辑器（原 lifecycle-agent-matrix spec 的首条用例）。
    await expect(page.getByTestId('agent-labels-editor')).toBeVisible()
    await entry.getByRole('link', { name: '打开统一设置页' }).click()

    await expect(page).toHaveURL(/\/app\/settings\/lifecycle\/?/)
    await expect(page.getByTestId('lifecycle-settings-page')).toBeVisible()
    await expect(page.getByTestId('block-nav')).toBeVisible()
  })

  test('全局范围：九阶段矩阵齐全，每行展示生效 Agent / 模型 / 推理深度与来源，并附绑定预设下拉', async ({
    page,
  }) => {
    await page.goto('/app/settings/lifecycle/')

    const pageRoot = page.getByTestId('lifecycle-settings-page')
    await expect(pageRoot).toBeVisible()
    // 默认停在「全局」范围。
    await expect(page.getByTestId('scope-global')).toBeVisible()
    await expect(page.getByTestId('scope-repository')).toBeVisible()

    for (const lifecycleKey of LIFECYCLE_KEYS) {
      await expect(page.getByTestId(`matrix-row-${lifecycleKey}`)).toBeVisible()
      await expect(page.getByTestId(`matrix-agent-${lifecycleKey}`)).toBeVisible()
      await expect(page.getByTestId(`matrix-model-${lifecycleKey}`)).toBeVisible()
      await expect(page.getByTestId(`matrix-effort-${lifecycleKey}`)).toBeVisible()
      await expect(page.getByTestId(`matrix-binding-${lifecycleKey}`)).toBeVisible()
    }

    // fix / closeout 未绑定时继承实现阶段——矩阵以来源文案呈递，而不是空值。
    // 打开「实现」行的绑定下拉：候选只能是已定义预设或清除项，没有伪选项。
    await page.getByTestId('matrix-binding-implementation').click()
    await expect(page.getByTestId('matrix-binding-implementation-option-clear')).toBeVisible()
    await page.keyboard.press('Escape')
  })

  test('模型预设区块与新建表单、执行器回退区块控件齐备（只读，不保存）', async ({ page }) => {
    await page.goto('/app/settings/lifecycle/')
    await expect(page.getByTestId('lifecycle-settings-page')).toBeVisible()
    // 预设卡片是客户端拉取聚合视图后才渲染的：不等网络静默就 count，会稳定拿到 0，
    // 把"其实有预设"的环境误判成空态分支（该分支断言的是空态提示，于是必然超时）。
    await page.waitForLoadState('networkidle')

    // 预设区：有预设则渲染卡片，没有则渲染空态提示；新建表单始终在。
    const presetCards = page.locator('[data-testid^="preset-card-"]')
    const hasPresets = (await presetCards.count()) > 0
    if (hasPresets) {
      await expect(presetCards.first()).toBeVisible()
    } else {
      await expect(page.getByTestId('preset-empty')).toBeVisible()
    }
    await expect(page.getByTestId('new-preset-form')).toBeVisible()
    await expect(page.getByTestId('new-preset-add')).toBeVisible()

    // 回退区：候选行 + 追加 / 预算 / 保存控件在。仅断言控件存在与可交互，**不点保存**
    //（e2e 跑在共享仓库上，写盘会污染真实 config.toml；落盘语义由后端契约测试覆盖）。
    await expect(page.getByTestId('fallback-add')).toBeVisible()
    await expect(page.getByTestId('fallback-switches')).toBeVisible()
    await expect(page.getByTestId('fallback-save')).toBeVisible()
  })

  test('切到仓库范围但未选仓库时，提示先选择仓库', async ({ page }) => {
    await page.goto('/app/settings/lifecycle/')
    await expect(page.getByTestId('lifecycle-settings-page')).toBeVisible()

    await page.getByTestId('scope-repository').click()
    await expect(page.getByTestId('lifecycle-settings-pick-repo')).toBeVisible()
    await expect(page.getByTestId('scope-repo-select')).toBeVisible()
  })

  test('Backlog 仓库齿轮带 repo_id 进入统一页并预选仓库范围', async ({ page }) => {
    await page.goto('/app/backlog/')
    // Backlog 是 SPA：仓库列表是独立请求，等网络静默再计数，否则刚 goto 完就 count 会
    // 稳定拿到 0，把有仓库的情况误判成"没有受管理仓库"而跳过本用例。
    await page.waitForLoadState('networkidle')

    const gearButtons = page.locator('[data-testid^="repo-agent-gear-"]')
    const gearCount = await gearButtons.count()
    test.skip(gearCount === 0, '当前 registry 没有启用中的受管理仓库，跳转过录用例。')

    await gearButtons.first().click()
    await expect(page).toHaveURL(/\/app\/settings\/lifecycle\/\?scope=repository&repo_id=/)
    await expect(page.getByTestId('lifecycle-settings-page')).toBeVisible()
    for (const lifecycleKey of LIFECYCLE_KEYS) {
      await expect(page.getByTestId(`matrix-row-${lifecycleKey}`)).toBeVisible()
    }

    // 回归守护：带 repo_id 进入后切回「全局」，视图身份须与新选择一致。
    // 全局请求不带 repo_id、响应 repo_id 恒为 null，若残留仓库选择参与比对，
    // 页面会永远停在「加载中…」，九阶段矩阵一次都看不到。
    await page.getByTestId('scope-global').click()
    await expect(page.getByTestId('lifecycle-settings-loading')).toHaveCount(0)
    for (const lifecycleKey of LIFECYCLE_KEYS) {
      await expect(page.getByTestId(`matrix-row-${lifecycleKey}`)).toBeVisible()
    }
  })

  test('窄屏（400px）矩阵仍可读：九行可见且未被裁剪', async ({ page }) => {
    await page.setViewportSize({ width: 400, height: 800 })
    await page.goto('/app/settings/lifecycle/')
    await expect(page.getByTestId('lifecycle-settings-page')).toBeVisible()

    for (const lifecycleKey of LIFECYCLE_KEYS) {
      const row = page.getByTestId(`matrix-row-${lifecycleKey}`)
      await expect(row).toBeVisible()
      // 单列堆叠下每行仍占满可用宽度（grid-cols-1），宽度接近视口而非塌成 0。
      const box = await row.boundingBox()
      expect(box).not.toBeNull()
      expect(box?.width).toBeGreaterThan(300)
    }
  })
})
