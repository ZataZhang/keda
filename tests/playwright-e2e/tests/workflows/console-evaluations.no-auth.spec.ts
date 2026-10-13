/**
 * Console「Evaluations」页用户流程验证（真实页面 + 评测端点 mock）。
 *
 * Next.js 页面、侧边导航、详情面板与授权对话框保持真实；只拦截本 PRD 新增的
 * `/console/evaluation/*` 端点返回确定性状态，覆盖三条红线路径：
 * 创建/启动不触发评审、授权对话框预览即所发内容、deny 后出现 unauthorized
 * 行且状态收尾。
 *
 * Run with:
 *   PLAYWRIGHT_SKIP_STACK_BOOT=1 PLAYWRIGHT_STACK_MODE=dev \
 *   PLAYWRIGHT_BASE_URL=http://127.0.0.1:8765 \
 *   npm run test:no-auth -- console-evaluations
 */

import { expect, test } from '@playwright/test'
import type { Page, Route } from '@playwright/test'

const API_ROOT = '/api/v1/agent-runner/console/evaluation'

/** mock 的 run 行：仅声明流程断言需要的窄字段，其余按原样透传。 */
type MockRun = {
  run_id: string
  status: string
  manifest_hash: string | null
  judge_preset_snapshots: { preset_name: string; model?: string }[]
  [key: string]: unknown
}

/** 构造一条 run 行（与后端 DTO 字段同名）。 */
function buildRun(overrides: Record<string, unknown>): MockRun {
  return {
    run_id: 'eval-created01',
    suite_id: 'demo-suite',
    suite_version: '1.0.0',
    suite_hash: 'sha-demo',
    rubric_version: 'v1',
    repo_path: '/tmp/demo-repo',
    base_ref: 'main',
    base_commit: 'abc123def456',
    base_tree_hash: 'tree-demo',
    execution_preset_snapshots: [{ preset_name: 'alpha', agent: 'codex', model: 'gpt-a' }],
    judge_preset_snapshots: [{ preset_name: 'judge-1', model: 'gpt-judge' }],
    status: 'created',
    manifest_hash: null,
    case_ids: ['case-a'],
    check_timeout_seconds: 60,
    error: null,
    created_at: '2026-10-13T00:00:00+00:00',
    updated_at: '2026-10-13T00:00:00+00:00',
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

/** 待授权 run 的候选结果行（res-1 成功、res-2 执行超时）。 */
const RESULTS = [
  {
    result_id: 'res-1',
    run_id: 'eval-await01',
    case_id: 'case-a',
    execution_preset_id: 'alpha',
    execution_model_snapshot: { preset_name: 'alpha' },
    base_tree_hash: 'tree-demo',
    worktree_path: null,
    result_status: 'succeeded',
    check_status: 'passed',
    check_recipe: 'pytest',
    check_exit_code: 0,
    check_output_summary: '3 passed',
    agent_summary: '实现完成',
    duration_seconds: 42,
    usage_summary: { total_tokens: 1200 },
    cost_status: 'unknown',
    diff_hash: 'diff-1',
    error: null,
    created_at: '2026-10-13T00:10:00+00:00',
    updated_at: '2026-10-13T00:11:00+00:00',
  },
  {
    result_id: 'res-2',
    run_id: 'eval-await01',
    case_id: 'case-a',
    execution_preset_id: 'beta',
    execution_model_snapshot: { preset_name: 'beta' },
    base_tree_hash: 'tree-demo',
    worktree_path: null,
    result_status: 'timeout',
    check_status: 'not_run',
    check_recipe: 'pytest',
    check_exit_code: null,
    check_output_summary: null,
    agent_summary: null,
    duration_seconds: 900,
    usage_summary: null,
    cost_status: 'unknown',
    diff_hash: null,
    error: 'agent 超时',
    created_at: '2026-10-13T00:10:00+00:00',
    updated_at: '2026-10-13T00:25:00+00:00',
  },
]

/** 逐评审评分行：judge-1 有效、judge-2 拒答（失败态不折算为 0 分）。 */
const AWAITING_SCORES = [
  {
    score_id: 'score-valid',
    run_id: 'eval-await01',
    result_id: 'res-1',
    case_id: 'case-a',
    execution_preset_id: 'alpha',
    judge_preset_id: 'judge-1',
    judge_model_snapshot: { preset_name: 'judge-1', model: 'gpt-judge' },
    rubric_version: 'v1',
    score_status: 'valid',
    dimension_scores: {
      correctness: 4,
      completeness: 3,
      code_quality: 5,
      verification: 4,
    },
    rationale_summary: 'correctness=4: 满足任务要求',
    response_hash: 'resp-1',
    duration_seconds: 8,
    usage_summary: { total_tokens: 900 },
    error: null,
    created_at: '2026-10-13T00:30:00+00:00',
  },
  {
    score_id: 'score-refusal',
    run_id: 'eval-await01',
    result_id: 'res-1',
    case_id: 'case-a',
    execution_preset_id: 'alpha',
    judge_preset_id: 'judge-2',
    judge_model_snapshot: { preset_name: 'judge-2', model: 'other-judge' },
    rubric_version: 'v1',
    score_status: 'refusal',
    dimension_scores: {},
    rationale_summary: '评审模型显式拒答',
    response_hash: 'resp-2',
    duration_seconds: 6,
    usage_summary: null,
    error: null,
    created_at: '2026-10-13T00:30:00+00:00',
  },
]

const AWAITING_SUMMARY = {
  aggregates: [
    { dimension: 'correctness', median: 4, minimum: 4, maximum: 4, valid_count: 1 },
    { dimension: 'completeness', median: 3, minimum: 3, maximum: 3, valid_count: 1 },
    { dimension: 'code_quality', median: 5, minimum: 5, maximum: 5, valid_count: 1 },
    { dimension: 'verification', median: 4, minimum: 4, maximum: 4, valid_count: 1 },
  ],
  by_execution_preset: {
    alpha: [
      { dimension: 'correctness', median: 4, minimum: 4, maximum: 4, valid_count: 1 },
      { dimension: 'completeness', median: 3, minimum: 3, maximum: 3, valid_count: 1 },
      { dimension: 'code_quality', median: 5, minimum: 5, maximum: 5, valid_count: 1 },
      { dimension: 'verification', median: 4, minimum: 4, maximum: 4, valid_count: 1 },
    ],
  },
  status_counts: { refusal: 1, 'result:timeout': 1 },
  valid_judge_count: 1,
}

const EMPTY_AGGREGATES = [
  { dimension: 'correctness', median: null, minimum: null, maximum: null, valid_count: 0 },
  { dimension: 'completeness', median: null, minimum: null, maximum: null, valid_count: 0 },
  { dimension: 'code_quality', median: null, minimum: null, maximum: null, valid_count: 0 },
  { dimension: 'verification', median: null, minimum: null, maximum: null, valid_count: 0 },
]

const PREVIEW_PAYLOAD_TEXT =
  '{\n  "case_prompt": "实现 FizzBuzz",\n  "diff": "+def fizzbuzz(n): ...",\n  "rubric": "default-rubric@v1"\n}'

/** 评测端点的可变 mock 状态（页面轮询与动作后重读要看到新状态）。 */
type EvaluationState = {
  runs: ReturnType<typeof buildRun>[]
  requests: { method: string; path: string; body?: unknown }[]
  consentResponse: { status: number; body: Record<string, unknown> } | null
}

/**
 * 拦截全部 `/console/evaluation/*` 请求并按可变状态应答。
 *
 * @param page - Playwright 页面对象。
 * @returns mock 状态引用，供断言请求序列与改写应答。
 */
async function mockEvaluationApi(page: Page): Promise<EvaluationState> {
  const state: EvaluationState = {
    runs: [
      buildRun({ run_id: 'eval-await01', status: 'awaiting_judge_consent', manifest_hash: 'hash-await' }),
    ],
    requests: [],
    consentResponse: null,
  }

  const suites = {
    suites: [
      {
        file: 'demo.yaml',
        valid: true,
        suite_id: 'demo-suite',
        version: '1.0.0',
        title: '演示评测集',
        repo_path: '/tmp/demo-repo',
        base_ref: 'main',
        content_hash: 'sha-demo',
        rubric_version: 'v1',
        check_timeout_seconds: 60,
        cases: [
          { case_id: 'case-a', prompt: '实现 FizzBuzz', check_recipe: 'pytest' },
        ],
        errors: [],
      },
    ],
  }
  const presets = {
    execution_presets: [
      { name: 'alpha', agent: 'codex', model: 'gpt-a', reasoning_effort: null, cost_status: 'unknown', cost_estimate: null },
      { name: 'beta', agent: 'claude', model: 'claude-b', reasoning_effort: 'high', cost_status: 'unknown', cost_estimate: null },
    ],
    judge_presets: [
      { name: 'judge-1', model: 'gpt-judge', temperature: 0, provider: 'openai', available: true, reason: null, api_key_env: 'OPENAI_API_KEY', cost_status: 'unknown' },
      { name: 'judge-offline', model: 'x', temperature: 0, provider: null, available: false, reason: '缺少凭据', api_key_env: null, cost_status: 'unknown' },
    ],
    default_judge_presets: ['judge-1'],
    max_concurrency: 2,
    agent_timeout_seconds: 900,
    judge_timeout_seconds: 120,
  }

  const fulfill = async (route: Route, status: number, body: unknown) => {
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  }

  await page.route(`${API_ROOT}/**`, async (route: Route) => {
    const request = route.request()
    const method = request.method()
    const url = new URL(request.url())
    const path = url.pathname.replace(API_ROOT, '')
    let body: unknown = null
    try {
      body = request.postDataJSON()
    } catch {
      body = undefined
    }
    state.requests.push({ method, path, body })

    if (path === '/suites') return fulfill(route, 200, suites)
    if (path === '/presets') return fulfill(route, 200, presets)
    if (path === '/runs' && method === 'GET') return fulfill(route, 200, { runs: state.runs })
    if (path === '/runs' && method === 'POST') {
      const created = buildRun({
        run_id: 'eval-created01',
        ...(body as Record<string, unknown>),
      })
      state.runs = [created, ...state.runs]
      return fulfill(route, 201, created)
    }
    const detailMatch = path.match(/^\/runs\/([^/]+)(?:\/(start|cancel|judge-preview|judge-consent))?$/)
    if (!detailMatch) return fulfill(route, 404, { detail: 'unexpected path' })
    const [, runId, action] = detailMatch
    const run = state.runs.find((item) => item.run_id === runId)
    if (!run) return fulfill(route, 404, { detail: `run '${runId}' 不存在` })

    if (action === undefined || action === '') {
      if (method === 'DELETE') {
        state.runs = state.runs.filter((item) => item.run_id !== runId)
        return fulfill(route, 200, { deleted: runId })
      }
      const awaiting = run.status === 'awaiting_judge_consent'
      return fulfill(route, 200, {
        run,
        results: awaiting ? RESULTS : [],
        scores: awaiting
          ? AWAITING_SCORES
          : run.status === 'completed'
            ? [
                ...AWAITING_SCORES,
                {
                  ...AWAITING_SCORES[0],
                  score_id: 'score-unauthorized',
                  judge_preset_id: 'judge-2',
                  score_status: 'unauthorized',
                  dimension_scores: {},
                  rationale_summary: null,
                  error: '用户拒绝外发',
                },
              ]
            : [],
        summary: awaiting || run.status === 'completed' ? AWAITING_SUMMARY : { aggregates: EMPTY_AGGREGATES, by_execution_preset: {}, status_counts: {}, valid_judge_count: 0 },
        payload_state: {
          is_active: ['created', 'queued', 'running', 'awaiting_judge_consent', 'judging'].includes(run.status),
          awaiting_consent: awaiting,
          judge_selected: run.judge_preset_snapshots.length > 0,
        },
      })
    }
    if (action === 'start' && method === 'POST') {
      run.status = 'queued'
      return fulfill(route, 200, run)
    }
    if (action === 'cancel' && method === 'POST') {
      run.status = 'cancelled'
      return fulfill(route, 200, run)
    }
    if (action === 'judge-preview' && method === 'GET') {
      if (run.status !== 'awaiting_judge_consent') {
        return fulfill(route, 409, { detail: `run 状态 '${run.status}' 没有待授权的评审 payload` })
      }
      return fulfill(route, 200, {
        run_id: runId,
        manifest_hash: 'hash-await',
        frozen_manifest_hash: 'hash-await',
        judge_targets: run.judge_preset_snapshots,
        entries: [
          {
            result_id: 'res-1',
            case_id: 'case-a',
            execution_preset_id: 'alpha',
            payload_file: 'res-1.json',
            payload_sha256: 'a'.repeat(64),
            size_bytes: PREVIEW_PAYLOAD_TEXT.length,
            payload_available: true,
            payload_preview: PREVIEW_PAYLOAD_TEXT,
            payload_truncated: false,
          },
        ],
        estimated_requests: 2,
        sensitive_notice: 'payload 含候选代码差异，可能包含敏感内容；发送后无法由本机撤回。',
      })
    }
    if (action === 'judge-consent' && method === 'POST') {
      if (state.consentResponse) {
        return fulfill(route, state.consentResponse.status, state.consentResponse.body)
      }
      const decision = (body as { decision?: string })?.decision
      run.status = decision === 'approve' ? 'judging' : 'completed'
      run.manifest_hash = 'hash-await'
      return fulfill(route, 200, run)
    }
    return fulfill(route, 405, { detail: 'method not allowed' })
  })

  return state
}

/** 打开评测页（真实静态导出路由）。 */
async function openEvaluationsPage(page: Page) {
  await page.goto('/app/evaluations/')
  await expect(page.getByRole('heading', { name: '模型评测' })).toBeVisible()
}

test.describe('Console 模型评测页', () => {
  test('从导航进入评测页并可创建/启动 run（创建与启动都不触发评审）', async ({ page }) => {
    const state = await mockEvaluationApi(page)
    await page.goto('/')
    await page.getByRole('link', { name: 'Evaluations' }).click()
    await expect(page.getByRole('heading', { name: '模型评测' })).toBeVisible()

    await page.selectOption('#evaluation-suite-select', 'demo-suite')
    await expect(page.getByText('演示评测集 · 仓库 /tmp/demo-repo')).toBeVisible()
    await page.getByRole('checkbox', { name: '执行预设 alpha' }).click()
    await page.getByRole('checkbox', { name: '执行预设 beta' }).click()
    // 页面按 default_judge_presets 预选 judge-1；点击反而会把已勾选项取消。
    await expect(page.getByRole('checkbox', { name: '评审预设 judge-1' })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: '评审预设 judge-offline' })).toBeDisabled()

    await page.getByTestId('evaluation-create').click()
    const createRequest = state.requests.find((item) => item.path === '/runs' && item.method === 'POST')
    expect(createRequest?.body).toMatchObject({
      suite_id: 'demo-suite',
      execution_presets: ['alpha', 'beta'],
      judge_presets: ['judge-1'],
    })
    const createdRow = page.locator('tr', { hasText: 'eval-created01' })
    await expect(createdRow).toContainText('待启动')

    await createdRow.click()
    const detail = page.getByTestId('evaluation-run-detail')
    await expect(detail).toContainText('eval-created01')
    await expect(detail).toContainText('候选尚未开始')
    await detail.getByRole('button', { name: '启动执行' }).click()
    await expect(detail.getByRole('button', { name: '启动执行' })).toHaveCount(0)

    // 红线：整个流程没有触碰 judge-preview / judge-consent 端点。
    expect(
      state.requests.filter((item) => item.path.includes('judge-')).length,
    ).toBe(0)
  })

  test('待授权 run 展示聚合与失败态，拒绝外发后收尾并留 unauthorized 行', async ({ page }) => {
    const state = await mockEvaluationApi(page)
    await openEvaluationsPage(page)

    await page.locator('tr', { hasText: 'eval-await01' }).click()
    const detail = page.getByTestId('evaluation-run-detail')
    await expect(detail).toContainText('待授权评审')
    await expect(detail).toContainText('有效评审 × 1')
    await expect(page.getByTestId('evaluation-aggregate-correctness').first()).toContainText('4')
    await expect(page.getByTestId('evaluation-aggregate-correctness').first()).toContainText('N 1')
    await expect(detail).toContainText('refusal × 1')
    await expect(detail).toContainText('评审拒绝')
    await expect(detail).toContainText('timeout')

    await detail.getByRole('button', { name: '查看 payload 并授权' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('评审外发授权')).toBeVisible()
    await expect(dialog.locator('code').filter({ hasText: 'hash-await' })).toHaveCount(2)
    await dialog.locator('details').first().click()
    await expect(dialog.getByText('+def fizzbuzz(n): ...')).toBeVisible()
    await expect(dialog).toContainText('预计请求数：2')
    await expect(dialog).toContainText('payload 含候选代码差异')

    await dialog.getByRole('button', { name: '拒绝外发' }).click()
    await expect(dialog).toBeHidden()
    const consentRequest = state.requests.find((item) => item.path.endsWith('/judge-consent'))
    expect(consentRequest?.body).toMatchObject({ decision: 'deny', manifest_hash: 'hash-await' })
    await expect(detail).toContainText('已完成')
    await expect(detail).toContainText('未授权外发')
  })

  test('hash 过期时授权返回 409，对话框保留错误并重新预览', async ({ page }) => {
    const state = await mockEvaluationApi(page)
    state.consentResponse = {
      status: 409,
      body: { detail: "授权请求的 manifest hash 与 run 冻结值不一致，请重新预览后授权。" },
    }
    await openEvaluationsPage(page)
    await page.locator('tr', { hasText: 'eval-await01' }).click()
    await page
      .getByTestId('evaluation-run-detail')
      .getByRole('button', { name: '查看 payload 并授权' })
      .click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: '确认外发并评审' }).click()
    // 提交失败经 sonner toast 呈报（对话框本身保持打开并自动重新预览）。
    const toaster = page.locator('[data-sonner-toaster]')
    await expect(
      toaster.getByText('授权提交失败：授权请求的 manifest hash 与 run 冻结值不一致', { exact: false })
    ).toBeVisible()
    await expect(dialog).toBeVisible()
    // run 未被改写：仍处于待授权。
    await expect(page.getByTestId('evaluation-run-detail')).toContainText('待授权评审')
  })
})
