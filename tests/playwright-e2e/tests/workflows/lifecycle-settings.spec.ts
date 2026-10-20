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
 *
 * 可用性标记（agent-availability PRD 的 rv-4 自动化部分）：最后一条用例把页面上的
 * 「未安装 / 已安装路径」标记与后端 `agent_availability` 逐行对照——前端漏接这段事实时
 * 标记整体消失，断言即失败；本机没装的 agent 由真实机器状态决定，不做 stub。
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

/** `agent_availability` 条目：core 与 Console 共用的那份可用性事实。 */
interface AgentAvailabilityFact {
  agent: string
  bin: string
  available: boolean
  bin_path: string | null
}

/** 聚合设置视图里本用例关心的字段（矩阵行 + 注册表 + 可用性事实）。 */
interface LifecycleSettingsFacts {
  agents: string[]
  agent_availability?: AgentAvailabilityFact[]
  lifecycles: { key: string; effective_agent: string | null }[]
}

/** Backlog 仓库选择器读的 registry 列表（转录用例只用这两个字段）。 */
interface RegistryRepositoryEntry {
  repo_id: string
  enabled: boolean
}

interface RegistryRepositories {
  repositories?: RegistryRepositoryEntry[]
}

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
    await page.getByTestId('settings-tab-presets').click()

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

    // 切到执行器回退 Tab；候选行 + 追加 / 预算 / 保存控件在。仅断言控件存在与可交互，**不点保存**
    //（e2e 跑在共享仓库上，写盘会污染真实 config.toml；落盘语义由后端契约测试覆盖）。
    await page.getByTestId('settings-tab-fallback').click()
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
    // Backlog 是 SPA：仓库列表是独立请求，等网络静默，否则刚 goto 完齿轮还不在 DOM 里。
    await page.waitForLoadState('networkidle')

    // registry 也列出停用的仓库，且它们的齿轮照样可点——点进停用仓库得到的是
    // "仓库 'x' 不存在或未启用" 而不是矩阵。本页头声明的前提是**已启用**的受管理仓库，
    // 所以按 API 的 enabled 挑齿轮，而不是拿第一个齿轮赌机器上仓库的启停状态。
    const reposResponse = await page.request.get('/api/v1/agent-runner/repositories')
    expect(reposResponse.ok(), `repositories 端点非 200：${await reposResponse.text()}`).toBe(true)
    const registry = (await reposResponse.json()) as RegistryRepositories
    const enabledRepoId = (registry.repositories ?? []).find((entry) => entry.enabled)?.repo_id
    test.skip(!enabledRepoId, '当前 registry 没有启用中的受管理仓库，跳转过录用例。')

    const gear = page.getByTestId(`repo-agent-gear-${enabledRepoId}`)
    await expect(gear).toBeVisible()
    await gear.click()
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

  test('矩阵按后端下发的可用性事实标注每个生效 Agent：未安装点名可执行文件，已安装给路径', async ({
    page,
  }) => {
    await page.goto('/app/settings/lifecycle/')
    await expect(page.getByTestId('lifecycle-settings-page')).toBeVisible()
    await page.waitForLoadState('networkidle')

    const response = await page.request.get('/api/v1/agent-runner/lifecycle-settings?scope=global')
    expect(response.ok(), `lifecycle-settings 端点非 200：${await response.text()}`).toBe(true)
    const view = (await response.json()) as LifecycleSettingsFacts

    // 契约：可用性条目覆盖注册表里的每一个名字，且 bin_path 与 available 同起同落。
    const availabilityByAgent = new Map(
      (view.agent_availability ?? []).map((fact) => [fact.agent, fact]),
    )
    expect(view.agent_availability, '视图未下发 agent_availability').toBeDefined()
    for (const agentName of view.agents) {
      const fact = availabilityByAgent.get(agentName)
      expect(fact, `注册表里的 ${agentName} 没有对应的可用性条目`).toBeDefined()
      expect(Boolean(fact?.available)).toBe(fact?.bin_path !== null)
    }

    for (const row of view.lifecycles) {
      const pathMark = page.getByTestId(`matrix-agent-path-${row.key}`)
      const missingMark = page.getByTestId(`matrix-agent-missing-${row.key}`)
      const fact = row.effective_agent ? availabilityByAgent.get(row.effective_agent) : undefined
      if (!fact) {
        // 生效值没解析到具体 agent（auto / 未声明）或后端未下发事实：页面不猜测。
        await expect(pathMark).toHaveCount(0)
        await expect(missingMark).toHaveCount(0)
        continue
      }
      if (fact.available) {
        await expect(pathMark).toContainText(String(fact.bin_path))
        await expect(missingMark).toHaveCount(0)
      } else {
        await expect(missingMark).toContainText('未安装')
        await expect(missingMark).toContainText(fact.bin)
        await expect(pathMark).toHaveCount(0)
      }
    }

    // 下拉候选共用同一份事实：不可用的 agent 在候选里点名"未安装"，可用的不带后缀。
    await page.getByTestId('settings-tab-fallback').click()
    await page.getByTestId('fallback-add').click()
    for (const agentName of view.agents) {
      const option = page.getByTestId(`fallback-add-${agentName}`)
      if ((await option.count()) === 0) continue
      const label = await option.innerText()
      if (availabilityByAgent.get(agentName)?.available === false) {
        expect(label, `未安装的 ${agentName} 在候选里没有任何提示`).toContain('未安装')
      } else {
        expect(label).not.toContain('未安装')
      }
    }
    await page.keyboard.press('Escape')
  })
})
