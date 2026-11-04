/*
 * PRD 生命周期观测与执行分析 · 交互原型状态模型
 *
 * 说明：本文件是**静态 fixture 驱动**的原型脚本，不含任何随机数、定时器或网络请求，
 * 也不接入真实 API / SQLite。所有数字与事件都是为了让评审确认信息层级与耗时口径而预先写死的，
 * 不代表生产环境已经具备这些能力。fixture 字段命名对齐后端只读契约
 * （lifecycle detail 与 console stats），便于实施时按同一形状对接。
 *
 * 时间线部分的展示词汇与分组/去噪规则镜像生产组件
 * （frontend-public/components/backlog/prd-lifecycle-timeline.tsx 与 prd-lifecycle-format.ts）：
 * 改任何一处文案或规则时两边都要一起改，否则原型确认的不是将要上线的形态。
 */
(() => {
  // 稳定 run id：同一 PRD 的历史跨重试共享它，Issue 编号只是外部关联。
  const RUN_ID = 'prdrun_01K5R4Z7Q9';
  const PRD_PATH = 'tasks/pending/P1-FEAT-20260921-161621-prd-lifecycle-observability.md';
  // 执行会话标识：写侧在 runner 会话上下文里给事件补的 invocation_run 值（Issue #273 新增）。
  const SESSION_A = 'keda-main#417#session-a';
  const SESSION_B = 'keda-main#417#session-b';
  const SESSION_RUN_KEY = 'invocation_run';

  // current_phase 闭集 → 中文展示名（与后端枚举一一对应）。
  const PHASE_LABELS = {
    none: '未开始', queued: '排队中', executing: '执行中', validating: '验证中',
    reviewing: '审阅中', merging: '合并中', blocked: '阻塞中', failed: '失败', completed: '已完成',
  };

  // event_type 闭集 → 中文展示名（与后端 / 生产前端同一份词汇）。
  const EVENT_TYPE_LABELS = {
    queued: '已进入队列', started: '开始执行', claimed: '已被领取', attempt: 'Agent 尝试',
    retry: '重试', recovered: '已恢复', implementation_completed: '实现完成',
    validation_started: '开始验证', validation_passed: '验证通过', validation_failed: '验证失败',
    review_started: '开始审阅', review_passed: '审阅通过', review_failed: '审阅失败',
    merge_started: '开始合并', merged: '已合并', archived: '已归档', blocked: '已阻塞',
    unblocked: '已解除阻塞', failed: '失败', agent_token_usage: 'Token 用量',
  };

  // 徽章语义状态覆盖项：事件类型名与状态名大多相同，只覆盖需要「状态化」的少数值。
  const STATUS_LABEL_OVERRIDES = { queued: '排队中', completed: '已完成', failed: '已失败' };

  const PHASE_BADGE_VARIANTS = {
    queued: 'default', executing: 'running', validating: 'supervising', reviewing: 'review',
    merging: 'supervising', blocked: 'blocked', failed: 'error', completed: 'ready', none: 'default',
  };
  const STATUS_BADGE_VARIANTS = {
    queued: 'default', started: 'running', claimed: 'supervising', attempt: 'running',
    retry: 'warning', recovered: 'ready', implementation_completed: 'supervising',
    validation_started: 'supervising', validation_passed: 'ready', validation_failed: 'error',
    review_started: 'review', review_passed: 'ready', review_failed: 'error',
    merge_started: 'supervising', completed: 'ready', archived: 'ready', blocked: 'blocked',
    unblocked: 'running', failed: 'error',
  };

  // 调用行词汇（阶段 / 角色 / 终态），与运行详情弹窗共用同一份口径。
  const INVOCATION_PHASE_LABELS = {
    implementation: '编写代码', fix: '修复问题', review: '代码审查', review_repair: '按审查意见修复',
    verification: '运行验证', verification_recovery: '修复验证问题', rebase_recovery: '同步分支并处理冲突',
    closeout: '交付收尾', supervisor: '自动检查进度', supervisor_repair: '检查发现问题后的修复',
    content_generation: '生成任务内容', unspecified: 'Agent 工作',
  };
  const INVOCATION_ROLE_LABELS = {
    implementer: '负责修改代码', fixer: '负责修复问题', reviewer: '负责审查代码',
    verifier: '负责运行验证', supervisor: '负责检查进度', content_generator: '负责生成内容',
    unspecified: '角色未记录',
  };
  const INVOCATION_OUTCOME_BADGES = {
    ok: { label: '正常退出', variant: 'ready' }, failed: { label: '异常退出', variant: 'error' },
    error: { label: '执行报错', variant: 'error' }, timeout: { label: '超时', variant: 'warning' },
    incomplete: { label: '记录不完整', variant: 'warning' }, unclosed: { label: '未闭合', variant: 'default' },
  };

  // 纯观测事件（Token 用量）：不推进阶段，徽章必须中性，禁止回落成「未开始」。
  const OBSERVATION_EVENT_TYPES = new Set(['agent_token_usage']);
  // 同秒成对出现、应折叠成一行的事件类型组（展示层去噪，不删事件）。
  const SAME_SECOND_MERGE_GROUPS = [['started', 'claimed'], ['attempt', 'retry']];
  // attempt detail 里表示「这次没失败」的字面值，摘要中不作为失败类别展示。
  const SUCCESS_FAILURE_TYPE = 'success';
  const ATTEMPT_FAMILY_EVENT_TYPES = new Set(['attempt', 'retry', 'recovered']);

  // 用于演示的仓库级统计聚合结果（GET /console/stats/prd-lifecycle）。
  const STATS_FIXTURE = {
    repo_id: 'keda-main',
    window_days: 30,
    completed_runs: 18,
    average_end_to_end_seconds: 12360, // 3 小时 26 分
    median_end_to_end_seconds: 10260, // 2 小时 51 分
    p90_end_to_end_seconds: 25920, // 7 小时 12 分
    average_blocked_seconds: 2520, // 42 分钟
    bottleneck_phase: '审阅等待',
    bottleneck_phase_seconds: 1320, // 22 分钟
    unlinked_run_count: 3, // 只有 Issue 编号、无法可靠归属 PRD 的旧记录
    runs: [
      // dynamic:true 的行会跟随当前场景的实时状态（进行中 → 阻塞中）重绘。
      { dynamic: true },
      { name: 'Backlog PRD 控制与验收证据', issue: 402, status: 'done', statusLabel: '已归档', start: '09-20 09:18', end: '09-20 12:02', active: '2h 09m', waiting: '35m', total: '2h 44m', retry: 0 },
      { name: '生命周期 Agent 矩阵', issue: 395, status: 'done', statusLabel: '已归档', start: '09-18 10:11', end: '09-18 16:39', active: '4h 51m', waiting: '1h 37m', total: '6h 28m', retry: 2 },
      { name: 'Dashboard 快照同步', issue: 371, status: 'failed', statusLabel: '失败', start: '09-16 14:08', end: '验证失败', active: '1h 06m', waiting: '11m', total: '1h 17m', retry: 3 },
      { name: '旧运行记录', issue: 288, status: 'legacy', statusLabel: '未关联 PRD', start: '09-10 08:20', end: '已结束', active: '52m', waiting: '9m', total: '1h 01m', retry: 0 },
    ],
  };

  /**
   * 调用明细行的稳定 fixture，形状镜像 PrdInvocationRow（后端 DTO）。
   *
   * `started_at` / `finished_at` 只写时:分:秒（省略日期）是原型的可读性取舍，
   * 生产展示完整本地时间；耗时口径仍取 `duration_seconds`。
   */
  const INVOCATIONS = {
    aImplementation: {
      invocation_id: 'inv-a-1-implementation', run_id: SESSION_A, attempt_number: 1,
      phase: 'implementation', role: 'implementer', executor: 'codex', outcome: 'ok',
      failure_category: null, started_at: '13:55:10', finished_at: '13:57:10', duration_seconds: 120.0,
      requested_model: 'gpt-5-codex', reported_model: 'gpt-5-codex',
      token_usage: { input_tokens: 4200, output_tokens: 800 },
      retry_of: null, retry_reason: null, exit_code: 0, log_locator: 'logs/inv-a-1-implementation.log',
    },
    aVerification: {
      invocation_id: 'inv-a-1-verification', run_id: SESSION_A, attempt_number: 1,
      phase: 'verification', role: 'verifier', executor: 'codex', outcome: 'failed',
      failure_category: 'non_zero_exit', started_at: '14:41:00', finished_at: '14:42:00', duration_seconds: 60.0,
      requested_model: null, reported_model: 'gpt-5',
      token_usage: null, retry_of: 'inv-a-1-implementation',
      retry_reason: 'verification exited non-zero', exit_code: 1, log_locator: 'logs/inv-a-1-verification.log',
    },
    bImplementation: {
      invocation_id: 'inv-b-2-implementation', run_id: SESSION_B, attempt_number: 2,
      phase: 'implementation', role: 'implementer', executor: 'claude', outcome: 'ok',
      failure_category: null, started_at: '14:50:00', finished_at: '15:05:00', duration_seconds: 900.0,
      requested_model: 'claude-sonnet-4-5', reported_model: null,
      token_usage: { input_tokens: 21000, output_tokens: 3400, cache_read_input_tokens: 1800 },
      retry_of: null, retry_reason: null, exit_code: 0, log_locator: 'logs/inv-b-2-implementation.log',
    },
    // 未闭合调用：审阅调用仍在跑，结束时间与耗时都必须留空，不得估算。
    bReviewUnclosed: {
      invocation_id: 'inv-b-2-review', run_id: SESSION_B, attempt_number: 2,
      phase: 'review', role: 'reviewer', executor: 'claude', outcome: 'unclosed',
      failure_category: null, started_at: '15:31:00', finished_at: null, duration_seconds: null,
      requested_model: null, reported_model: null, token_usage: null,
      retry_of: null, retry_reason: null, exit_code: null, log_locator: null,
    },
    bFixTimeout: {
      invocation_id: 'inv-b-3-fix', run_id: SESSION_B, attempt_number: 3,
      phase: 'fix', role: 'fixer', executor: 'claude', outcome: 'timeout',
      failure_category: 'timeout', started_at: '15:20:00', finished_at: '15:32:00', duration_seconds: 720.0,
      requested_model: 'claude-sonnet-4-5', reported_model: 'claude-sonnet-4-5', token_usage: null,
      retry_of: 'inv-b-2-implementation', retry_reason: 'gate timeout', exit_code: null,
      log_locator: 'logs/inv-b-3-fix.log',
    },
    // 收尾调用不是 attempt 主体：账本里 attempt_number 恒为 null，仍必须看得见。
    aCloseout: {
      invocation_id: 'inv-a-closeout', run_id: SESSION_A, attempt_number: null,
      phase: 'closeout', role: 'unspecified', executor: 'codex', outcome: 'ok',
      failure_category: null, started_at: '14:43:00', finished_at: '14:43:40', duration_seconds: 40.0,
      requested_model: null, reported_model: null, token_usage: null,
      retry_of: null, retry_reason: null, exit_code: 0, log_locator: 'logs/inv-a-closeout.log',
    },
    // 账本里存在、但时间线没有该执行会话的生命周期事件：只能披露条数，不能猜归属。
    ledgerOnlySession: {
      invocation_id: 'inv-x-1-implementation', run_id: 'keda-main#417#session-x', attempt_number: 1,
      phase: 'implementation', role: 'implementer', executor: 'codex', outcome: 'timeout',
      failure_category: 'timeout', started_at: '11:20:00', finished_at: '11:29:00', duration_seconds: 540.0,
      requested_model: 'gpt-5', reported_model: null, token_usage: null,
      retry_of: null, retry_reason: null, exit_code: null, log_locator: null,
    },
  };

  // 正常轨迹的事件账本（同一份 fixture 也被「调用明细不可用」「旧记录」复用）。
  const NORMAL_EVENTS = [
    {
      // 队列事件写在执行会话上下文之外：没有 invocation_run，落到「未归属」而不是被猜进任何会话。
      time: '13:42:00', eventType: 'queued', phase: 'queued', status: 'queued', actor: 'backlog',
      detail: { trigger: 'manual' },
      note: '由 Backlog 手动启动，core 创建稳定 run 标识并追加首个事件。它发生在 runner 会话之前，所以没有执行会话标识。',
    },
    {
      time: '13:54:00', eventType: 'started', phase: 'executing', status: 'started', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A },
      note: 'runner 领取到 Issue #417，会话内首条事件；写侧把当前执行会话标识注入 detail。',
    },
    {
      time: '13:54:00', eventType: 'claimed', phase: 'executing', status: 'claimed', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A },
      note: '与「开始执行」同一秒落库的领取回执：展示层折叠成一行，原始事件仍在账本里，可单独点开。',
    },
    {
      time: '13:55:00', eventType: 'attempt', phase: 'executing', status: 'attempt', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A, agent: 'codex', attempt_number: 1, failure_type: 'verification_failed', recovered: false, duration_seconds: 2820 },
      note: '第一次 Agent 尝试，是会话 A 里的尝试分组锚点；展开后看到该尝试的两次进程调用。',
    },
    {
      time: '14:42:00', eventType: 'validation_failed', phase: 'validating', status: 'validation_failed', actor: 'verifier',
      detail: { [SESSION_RUN_KEY]: SESSION_A, failure_summary: '真实入口断言失败 1 项' },
      note: '真实入口断言失败；失败事件被保留，后续成功不会覆盖它。',
    },
    {
      time: '14:43:00', eventType: 'agent_token_usage', phase: 'none', status: 'none', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A, flow: 'verify', agent: 'codex', token_usage: { input_tokens: 1200, output_tokens: 300 } },
      note: '纯观测事件：不推进阶段、不占时长，徽章必须是中性的「观测记录」，不能回落成「未开始」。',
    },
    {
      // 刻意让 retry 早于同秒的 attempt 落盘：行首仍必须是「Agent 尝试」，展示口径不随写入顺序漂移。
      time: '14:49:00', eventType: 'retry', phase: 'executing', status: 'retry', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_B, agent: 'codex', attempt_number: 2, failure_type: 'success', recovered: true, duration_seconds: 2100 },
      note: '复用同一 PRD run 新建 attempt（新执行会话 B），重试标记折叠进尝试行。',
    },
    {
      time: '14:49:00', eventType: 'attempt', phase: 'executing', status: 'attempt', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_B, agent: 'codex', attempt_number: 2, failure_type: 'success', recovered: true, duration_seconds: 2100 },
      note: '第二次尝试，重新收集最终代码树证据。',
    },
    {
      time: '15:24:00', eventType: 'validation_passed', phase: 'validating', status: 'validation_passed', actor: 'verifier',
      detail: { [SESSION_RUN_KEY]: SESSION_B, checks_passed: 12 },
      note: '证据门禁通过，等待独立 verifier 返回审阅结论。',
    },
    {
      time: '15:31:00', eventType: 'review_started', phase: 'reviewing', status: 'review_started', actor: 'verifier',
      detail: { [SESSION_RUN_KEY]: SESSION_B, reviewer: 'independent-verifier' },
      note: '机器证据已齐，正在等待 verifier 返回结论（尚未结束，不伪造结束时间）。',
    },
  ];

  // 失败与重试场景的事件账本：三次尝试、同秒折叠、最终阻塞。
  const FAILURE_EVENTS = [
    {
      time: '13:42:00', eventType: 'queued', phase: 'queued', status: 'queued', actor: 'backlog',
      detail: { trigger: 'manual' },
      note: '由 Backlog 手动启动，创建稳定 PRD run 标识。',
    },
    {
      time: '13:54:00', eventType: 'started', phase: 'executing', status: 'started', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A },
      note: 'Issue #417 进入 agent/running，首次 attempt 使用 codex。',
    },
    {
      time: '13:54:00', eventType: 'claimed', phase: 'executing', status: 'claimed', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A },
      note: '与开始执行同秒的领取回执，折叠成一行。',
    },
    {
      time: '13:55:00', eventType: 'attempt', phase: 'executing', status: 'attempt', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A, agent: 'codex', attempt_number: 1, failure_type: 'verification_failed', recovered: false, duration_seconds: 2940 },
      note: '第一次尝试：真实入口断言连续失败。',
    },
    {
      time: '14:42:00', eventType: 'validation_failed', phase: 'validating', status: 'validation_failed', actor: 'verifier',
      detail: { [SESSION_RUN_KEY]: SESSION_A, failure_summary: '真实入口断言连续 3 次失败' },
      note: '失败事件与耗时全部保留，后续 attempt 只追加、不重写历史。',
    },
    {
      time: '14:44:00', eventType: 'retry', phase: 'executing', status: 'retry', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A, agent: 'codex', attempt_number: 2, failure_type: 'agent_error', recovered: false, duration_seconds: 900 },
      note: '同秒的 attempt/retry 折叠成尝试行上的标记。',
    },
    {
      time: '14:44:00', eventType: 'attempt', phase: 'executing', status: 'attempt', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A, agent: 'codex', attempt_number: 2, failure_type: 'agent_error', recovered: false, duration_seconds: 900 },
      note: '第二次尝试在执行器侧报错。',
    },
    {
      time: '15:11:00', eventType: 'attempt', phase: 'executing', status: 'attempt', actor: 'operator',
      detail: { [SESSION_RUN_KEY]: SESSION_B, agent: 'claude', attempt_number: 3, failure_type: 'success', recovered: true, duration_seconds: 1260 },
      note: '操作者关闭代码缺陷后在新执行会话里手动重试，不覆盖旧失败事件。',
    },
    {
      time: '15:32:00', eventType: 'blocked', phase: 'blocked', status: 'blocked', actor: 'system',
      detail: { [SESSION_RUN_KEY]: SESSION_B, error_summary: '上游 CI 服务不可用，等待恢复' },
      note: '阻塞时间单独累计，不计入有效执行时间。',
    },
  ];

  // 观测写入降级场景：事件缺失一条，历史完整性必须被标记而不是被假装完整。
  const DEGRADED_EVENTS = [
    {
      time: '13:42:00', eventType: 'queued', phase: 'queued', status: 'queued', actor: 'backlog',
      detail: { trigger: 'manual' },
      note: '由 Backlog 手动启动，创建稳定 PRD run 标识。',
    },
    {
      time: '13:54:00', eventType: 'started', phase: 'executing', status: 'started', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A },
      note: 'Issue #417 进入 agent/running。',
    },
    {
      time: '13:55:00', eventType: 'attempt', phase: 'executing', status: 'attempt', actor: 'runner',
      detail: { [SESSION_RUN_KEY]: SESSION_A, agent: 'codex', attempt_number: 1, failure_type: 'success', recovered: false, duration_seconds: 2820 },
      note: '第一次尝试，会话标识照常注入。',
    },
    {
      time: '14:42:00', eventType: 'failed', phase: 'executing', status: 'failed', actor: 'system',
      detail: { [SESSION_RUN_KEY]: SESSION_A, error_summary: '观测存储写入异常（不阻断主流程）' },
      note: '旁路观测存储异常，事件未落库；runner 继续执行，history_complete 置为 false。',
    },
    {
      time: '15:41:00', eventType: 'implementation_completed', phase: 'validating', status: 'implementation_completed', actor: 'codex',
      detail: { [SESSION_RUN_KEY]: SESSION_A },
      note: '主流程已完成实现并进入验证，但对应事件缺失，页面据此显示数据不完整告警。',
    },
  ];

  /** 去掉执行会话标识，得到本次改动之前的旧账本形态（attempt 族字段仍保留）。 */
  function withoutSessionIds(eventList) {
    return eventList.map((event) => {
      const legacyDetail = { ...event.detail };
      delete legacyDetail[SESSION_RUN_KEY];
      return { ...event, detail: legacyDetail };
    });
  }

  // 五套稳定场景：正常、失败重试、观测降级、调用明细不可用、旧记录（无归属）。
  const SCENARIOS = {
    normal: {
      statusLabel: '进行中', statusClass: 'running', phase: 'reviewing',
      inProgress: true, outcome: null, historyComplete: true, warning: '',
      durations: { end_to_end_seconds: 8280, active_seconds: 6120, waiting_seconds: 2160, blocked_seconds: 0 },
      attempts: '2 次', attemptNote: '1 次失败后自动恢复',
      invocations: {
        available: true, truncated: false,
        rows: [
          INVOCATIONS.aImplementation, INVOCATIONS.aVerification, INVOCATIONS.aCloseout,
          INVOCATIONS.bImplementation, INVOCATIONS.bReviewUnclosed, INVOCATIONS.ledgerOnlySession,
        ],
      },
      events: NORMAL_EVENTS,
    },
    failure: {
      statusLabel: '阻塞中', statusClass: 'blocked', phase: 'blocked',
      inProgress: true, outcome: null, historyComplete: true, warning: '',
      durations: { end_to_end_seconds: 9060, active_seconds: 5040, waiting_seconds: 1620, blocked_seconds: 2400 },
      attempts: '3 次', attemptNote: '2 次失败，1 次人工重试',
      // 长执行会撞到账本读取上限：只显示读到的部分，并单独披露截断。
      invocations: { available: true, truncated: true, rows: [INVOCATIONS.aImplementation, INVOCATIONS.aVerification] },
      events: FAILURE_EVENTS,
    },
    degraded: {
      statusLabel: '进行中', statusClass: 'running', phase: 'validating',
      inProgress: true, outcome: null, historyComplete: false,
      warning: '生命周期记录不完整：一次事件写入失败，时间线存在缺口。runner 主流程未被阻断，日志中已保留可诊断错误。',
      durations: { end_to_end_seconds: 4800, active_seconds: 3600, waiting_seconds: 1200, blocked_seconds: 0 },
      attempts: '1 次', attemptNote: '观测写入失败 1 次',
      invocations: { available: true, truncated: false, rows: [INVOCATIONS.aImplementation] },
      events: DEGRADED_EVENTS,
    },
    unavailable: {
      statusLabel: '进行中', statusClass: 'running', phase: 'reviewing',
      inProgress: true, outcome: null, historyComplete: true, warning: '',
      durations: { end_to_end_seconds: 8280, active_seconds: 6120, waiting_seconds: 2160, blocked_seconds: 0 },
      attempts: '2 次', attemptNote: '调用账本读取失败',
      // 读不到就是读不到：不显示调用序列，也不把「没读到」画成「没发生」。
      invocations: { available: false, truncated: false, rows: [] },
      events: NORMAL_EVENTS,
    },
    legacy: {
      statusLabel: '已归档', statusClass: 'done', phase: 'completed',
      inProgress: false, outcome: 'completed', historyComplete: true, warning: '',
      durations: { end_to_end_seconds: 7500, active_seconds: 5400, waiting_seconds: 2100, blocked_seconds: 0 },
      attempts: '2 次', attemptNote: '改动上线前的旧执行',
      // 旧响应里根本没有 invocations 区：整个时间线回落成单层平铺 + 明确披露。
      invocations: null,
      events: withoutSessionIds(NORMAL_EVENTS),
    },
  };

  const EVENT_MARKERS = { failure: '!', wait: '◷', success: '•' };
  const uiState = { view: 'dashboard', scenario: 'normal', expanded: new Set() };
  let eventRegistry = [];
  const elements = {
    timelineRoot: document.querySelector('#timeline-root'),
    inspector: document.querySelector('#event-inspector'),
    pageTitle: document.querySelector('#page-title'),
    phase: document.querySelector('#phase-value'),
    attempts: document.querySelector('#attempt-value'),
    attemptNote: document.querySelector('#attempt-note'),
    completeness: document.querySelector('#completeness-pill'),
    warning: document.querySelector('#data-warning'),
    runsBody: document.querySelector('#runs-body'),
    runsSearch: document.querySelector('#runs-search'),
    unlinkedNote: document.querySelector('#unlinked-note'),
  };

  const scenarioOf = () => SCENARIOS[uiState.scenario];

  /** 把秒数格式化为「N 小时 M 分」或「N 分钟」，与生产端展示一致。 */
  function formatDuration(totalSeconds) {
    const safeSeconds = Math.max(0, Math.floor(totalSeconds));
    if (safeSeconds < 3600) return `${Math.round(safeSeconds / 60)} 分钟`;
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    return `${hours} 小时 ${minutes} 分`;
  }

  /** 调用行/尝试行的紧凑耗时口径，与生产 formatLifecycleDuration 一致（null → —）。 */
  function formatLifecycleDuration(seconds) {
    if (seconds === null || !Number.isFinite(seconds)) return '—';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}m`;
  }

  /** 把 token 计数格式化为紧凑文本（820 / 1.2k）。 */
  function formatTokenCount(count) {
    return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
  }

  /** 把 token_usage 渲染为总量 + 明细；没有可用用量时返回 null（不显示 0）。 */
  function summarizeTokenUsage(value) {
    if (typeof value !== 'object' || value === null) return null;
    const readCount = (key) => (typeof value[key] === 'number' && Number.isFinite(value[key]) && value[key] >= 0 ? value[key] : null);
    const inputTokens = readCount('input_tokens');
    const outputTokens = readCount('output_tokens');
    const cacheRead = readCount('cache_read_input_tokens');
    const cacheCreation = readCount('cache_creation_input_tokens');
    if (inputTokens === null && outputTokens === null && cacheRead === null && cacheCreation === null) return null;
    const total = (inputTokens ?? 0) + (cacheRead ?? 0) + (cacheCreation ?? 0) + (outputTokens ?? 0);
    const parts = [
      `输入 ${formatTokenCount(inputTokens ?? 0)}`,
      `输出 ${formatTokenCount(outputTokens ?? 0)}`,
      `缓存读 ${formatTokenCount(cacheRead ?? 0)}`,
      `缓存写 ${formatTokenCount(cacheCreation ?? 0)}`,
    ];
    return `tokens=${formatTokenCount(total)}（${parts.join(' · ')}）`;
  }

  /** 事件语义状态的中文徽章标签（覆盖表优先，回落事件类型名，再回落原值）。 */
  function eventStatusLabel(status) {
    return STATUS_LABEL_OVERRIDES[status] ?? EVENT_TYPE_LABELS[status] ?? status;
  }

  /** 把 detail 里的单个值转成文本，缺失显示 —。 */
  function formatDetailValue(value) {
    if (value === undefined || value === null) return '—';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    return JSON.stringify(value);
  }

  /**
   * 把事件 detail 渲染为一行短摘要（与生产 summarizeEventDetail 同口径）。
   *
   * 会话标识是分组依据而不是可读信息，因此在摘要里被跳过；attempt 族与用量观测
   * 各有固定字段顺序，其余事件退化为 key=value 拼接。
   */
  function summarizeEventDetail(event) {
    const detail = event.detail ?? {};
    if (event.eventType === 'attempt' || event.eventType === 'retry' || event.eventType === 'recovered') {
      const parts = [
        detail.attempt_number === undefined ? '#?' : `#${detail.attempt_number}`,
        `agent=${formatDetailValue(detail.agent)}`,
        `failure=${formatDetailValue(detail.failure_type)}`,
        `recovered=${formatDetailValue(detail.recovered)}`,
      ];
      const tokens = summarizeTokenUsage(detail.token_usage);
      if (tokens !== null) parts.push(tokens);
      return parts.join(' ');
    }
    if (event.eventType === 'agent_token_usage') {
      const tokens = summarizeTokenUsage(detail.token_usage);
      return [`flow=${formatDetailValue(detail.flow)}`, `agent=${formatDetailValue(detail.agent)}`, tokens ?? 'tokens=—'].join(' ');
    }
    const entries = Object.entries(detail).filter(([key]) => key !== SESSION_RUN_KEY);
    if (entries.length === 0) return '—';
    return entries.map(([key, value]) => `${key}=${formatDetailValue(value)}`).join(' ');
  }

  /** 事件徽章：观测事件走中性文案，其余按语义状态（缺失时回落阶段）。 */
  function badgeOf(event) {
    if (OBSERVATION_EVENT_TYPES.has(event.eventType)) return { label: '观测记录', variant: 'default' };
    if (event.status && event.status !== 'none') {
      return { label: eventStatusLabel(event.status), variant: STATUS_BADGE_VARIANTS[event.status] ?? 'default' };
    }
    return { label: PHASE_LABELS[event.phase] ?? event.phase, variant: PHASE_BADGE_VARIANTS[event.phase] ?? 'default' };
  }

  /** 行首标记配色：失败与等待之外都是普通里程碑。 */
  function markerOf(event) {
    if (['validation_failed', 'review_failed', 'failed'].includes(event.status)) return 'failure';
    if (['queued', 'blocked', 'review_started'].includes(event.status)) return 'wait';
    return 'success';
  }

  /** 调用终态徽章；未知取值原样透出，不猜语义。 */
  function outcomeBadge(outcome) {
    return INVOCATION_OUTCOME_BADGES[outcome] ?? { label: `状态：${outcome}`, variant: 'default' };
  }

  /** 用准确但易读的文字说明模型名称来自哪里（三态，不互相冒充）。 */
  function describeInvocationModel(row) {
    const requested = row.requested_model;
    const reported = row.reported_model;
    if (requested && reported && requested !== reported) return `KC 下发：${requested}；执行器报告：${reported}`;
    if (requested && reported) return `KC 下发且执行器报告：${requested}`;
    if (requested) return `KC 下发：${requested}；执行器没有报告模型名称`;
    if (reported) return `执行器报告：${reported}；KC 没有单独指定模型`;
    return '无法确认具体模型：执行器没有上报，KC 也没有单独指定。';
  }

  /** 取得事件所属执行会话标识；旧事件没有该键时为 null（不猜）。 */
  function sessionKeyOf(event) {
    const rawRunId = event.detail?.[SESSION_RUN_KEY];
    return typeof rawRunId === 'string' && rawRunId ? rawRunId : null;
  }

  /** 取得事件的尝试编号；没有该字段时为 null。 */
  function attemptNumberOf(event) {
    const rawAttempt = event.detail?.attempt_number;
    return typeof rawAttempt === 'number' && Number.isFinite(rawAttempt) ? rawAttempt : null;
  }

  /**
   * 把同一秒成对出现的里程碑事件折叠成一行（只折叠声明过的组合）。
   *
   * 主事件取组内**声明顺序最靠前**的类型而不是先落库的那条，展示口径不随写入顺序漂移。
   */
  function foldSameSecondEvents(eventList) {
    const rows = [];
    const consumed = new Set();
    eventList.forEach((event, index) => {
      if (consumed.has(index)) return;
      const mergeGroup = SAME_SECOND_MERGE_GROUPS.find((group) => group.includes(event.eventType));
      if (!mergeGroup) {
        consumed.add(index);
        rows.push({ primary: event, folded: [] });
        return;
      }
      const clusterIndices = [];
      eventList.forEach((candidate, candidateIndex) => {
        if (!consumed.has(candidateIndex) && candidate.time === event.time && mergeGroup.includes(candidate.eventType)) {
          consumed.add(candidateIndex);
          clusterIndices.push(candidateIndex);
        }
      });
      const anchorIndex = clusterIndices.reduce((bestIndex, candidateIndex) => (
        mergeGroup.indexOf(eventList[candidateIndex].eventType) < mergeGroup.indexOf(eventList[bestIndex].eventType)
          ? candidateIndex
          : bestIndex
      ));
      rows.push({
        primary: eventList[anchorIndex],
        folded: clusterIndices.filter((candidateIndex) => candidateIndex !== anchorIndex).map((candidateIndex) => eventList[candidateIndex]),
      });
    });
    return rows;
  }

  /**
   * 按「执行会话 → Agent 尝试」两级归组事件（只按显式字段，不做时间窗推断）。
   *
   * 返回的 sessions 为空表示整条时间线都没有会话标识（旧数据），调用方据此回落平铺。
   */
  function groupSessions(eventList) {
    const sessions = [];
    const unattributed = [];
    eventList.forEach((event) => {
      const runId = sessionKeyOf(event);
      if (runId === null) {
        unattributed.push(event);
        return;
      }
      let session = sessions.find((item) => item.runId === runId);
      if (!session) {
        session = { runId, firstOccurredAt: event.time, eventRows: [], attempts: [] };
        sessions.push(session);
      }
      const attemptNumber = attemptNumberOf(event);
      if (attemptNumber === null) {
        session.eventRows.push(event);
        return;
      }
      let attempt = session.attempts.find((item) => item.attemptNumber === attemptNumber);
      if (!attempt) {
        attempt = { attemptNumber, eventRows: [], invocations: [] };
        session.attempts.push(attempt);
      }
      attempt.eventRows.push(event);
    });
    // 折叠只发生在同一分组内部：跨会话/跨尝试的同秒事件不属于「重复对」。
    return {
      sessions: sessions.map((session) => ({
        ...session,
        eventRows: foldSameSecondEvents(session.eventRows),
        attempts: session.attempts.map((attempt) => ({ ...attempt, eventRows: foldSameSecondEvents(attempt.eventRows) })),
      })),
      unattributedRows: foldSameSecondEvents(unattributed),
    };
  }

  /** 取某次尝试的调用行（顺序即账本顺序，前端不重排）。 */
  function invocationsForAttempt(runId, attemptNumber) {
    return (scenarioOf().invocations?.rows ?? []).filter(
      (row) => row.run_id === runId && row.attempt_number === attemptNumber,
    );
  }

  /**
   * 取某执行会话里「不属于任何已展示尝试」的调用行。
   *
   * `closeout` / `content_generation` 这类调用在账本里没有尝试编号，验证与收尾调用
   * 也可能落在没有事件的尝试上。它们同样是已落库事实，收进会话级「其他调用」，
   * 而不是让它们从页面上静默消失。
   */
  function otherInvocationsForSession(runId, attemptNumbers) {
    return (scenarioOf().invocations?.rows ?? []).filter(
      (row) =>
        row.run_id === runId &&
        (row.attempt_number === null || !attemptNumbers.has(row.attempt_number)),
    );
  }

  /** 账本里有、但时间线里没有对应执行会话标识的调用行数（只披露，不猜归属）。 */
  function unmatchedInvocationCount(sessionRunIds) {
    return (scenarioOf().invocations?.rows ?? []).filter(
      (row) => !sessionRunIds.has(row.run_id),
    ).length;
  }

  /** 尝试行摘要：agent / 失败类别 / 耗时 / 是否恢复，账本没写的不显示。 */
  function attemptSummaryParts(attempt) {
    const source = attempt.eventRows
      .flatMap((row) => [row.primary, ...row.folded])
      .find((event) => ATTEMPT_FAMILY_EVENT_TYPES.has(event.eventType));
    const detail = source?.detail ?? {};
    const parts = [];
    if (typeof detail.agent === 'string' && detail.agent) parts.push(detail.agent);
    if (typeof detail.failure_type === 'string' && detail.failure_type && detail.failure_type !== SUCCESS_FAILURE_TYPE) {
      parts.push(`失败类别 ${detail.failure_type}`);
    }
    if (typeof detail.duration_seconds === 'number') parts.push(`耗时 ${formatLifecycleDuration(detail.duration_seconds)}`);
    if (typeof detail.recovered === 'boolean') parts.push(detail.recovered ? '已恢复' : '未恢复');
    return parts.join(' · ');
  }

  /** 登记一行事件并返回它的 data 属性（点击任意折叠项都能打开原始 detail）。 */
  function registerEvent(event) {
    eventRegistry.push(event);
    return `data-event-ref="${eventRegistry.length - 1}"`;
  }

  /** 一行事件：时间、类型名、徽章、执行者、detail 摘要，以及同秒折叠项。 */
  function renderEventRow(row) {
    const primary = row.primary;
    const badge = badgeOf(primary);
    const marker = markerOf(primary);
    const folded = row.folded.map((foldedEvent) => {
      const foldedBadge = badgeOf(foldedEvent);
      return `<button type="button" class="folded-chip ${registerEvent(foldedEvent)}" aria-label="查看被折叠的事件：${EVENT_TYPE_LABELS[foldedEvent.eventType] ?? foldedEvent.eventType}">
          <span>+</span>${EVENT_TYPE_LABELS[foldedEvent.eventType] ?? foldedEvent.eventType}
          <span class="chip ${foldedBadge.variant}">${foldedBadge.label}</span>
        </button>`;
    }).join('');
    return `<li class="timeline-event${marker === 'failure' ? ' is-failure' : marker === 'wait' ? ' is-wait' : ''} hotspot-target" tabindex="0" role="button" aria-label="查看事件：${EVENT_TYPE_LABELS[primary.eventType] ?? primary.eventType}" ${registerEvent(primary)}>
        <time>${primary.time}</time><span class="event-marker">${EVENT_MARKERS[marker]}</span>
        <span class="timeline-copy"><strong>${EVENT_TYPE_LABELS[primary.eventType] ?? primary.eventType}
          <span class="chip ${badge.variant}">${badge.label}</span>
          <span class="event-actor">${primary.actor}</span></strong>
          <small>${summarizeEventDetail(primary)}</small></span>
        ${folded ? `<span class="event-fold">${folded}</span>` : ''}
      </li>`;
  }

  /** 一次进程调用行：阶段 / 角色 / 执行器 / 终态 / 耗时 / 模型三态 / 重试链 / 日志定位。 */
  function renderInvocationRow(row, index) {
    const badge = outcomeBadge(row.outcome);
    const tokens = summarizeTokenUsage(row.token_usage);
    const fields = [
      ['开始', row.started_at ?? '—'],
      ['结束', row.finished_at ?? '未闭合'],
      // 未闭合调用的 duration 恒为 null：显示未记录，不估算。
      ['耗时', row.duration_seconds === null ? '未记录' : formatLifecycleDuration(row.duration_seconds)],
      ['模型', describeInvocationModel(row)],
    ];
    if (row.failure_category) fields.push(['失败类别', row.failure_category]);
    if (row.exit_code !== null && row.exit_code !== undefined) fields.push(['退出码', row.exit_code]);
    if (tokens) fields.push(['用量', tokens]);
    if (row.log_locator) fields.push(['日志', row.log_locator]);
    return `<li class="invocation-row">
        <div class="invocation-row-head">
          <span>第 ${index + 1} 次调用</span>
          <strong>${INVOCATION_PHASE_LABELS[row.phase] ?? row.phase}</strong>
          <span>${row.executor || 'Agent 未记录'}${row.role ? ` · ${INVOCATION_ROLE_LABELS[row.role] ?? row.role}` : ''}</span>
          <span class="chip ${badge.variant}">${badge.label}</span>
          ${row.retry_of ? `<span class="invocation-retry">替代上一次调用${row.retry_reason ? `（${row.retry_reason}）` : ''}</span>` : ''}
        </div>
        <dl class="invocation-fields">${fields.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>
      </li>`;
  }

  /**
   * 尝试展开区空态的成因文案（与 prd-lifecycle-timeline 的判定同一份口径）。
   *
   * 「读不到」「没带这个区」「只读到最近一段」「确实没有记录」是四件事，说错成因
   * 会让人去修错误的一端——尤其是截断时这次尝试的调用**存在**，只是没被返回。
   */
  function attemptInvocationEmptyText() {
    const detail = scenarioOf().invocations;
    if (detail === null || detail === undefined) {
      return '响应未携带调用明细区（旧响应，或该 PRD 没有关联 Issue）：展开项不显示调用序列。';
    }
    if (!detail.available) {
      return '调用明细不可用：账本读取失败，展开项无法显示调用序列。';
    }
    if (detail.truncated) {
      return '调用记录已截断：读取只返回最近一段，这次尝试的调用序列可能未被包含。';
    }
    return '这次尝试没有可读到的调用记录（调用观测早于本次执行上线，或该阶段未产生进程调用）。';
  }

  /** 一个 Agent 尝试分组：尝试事件行 + 可展开的调用序列。 */
  function renderAttemptGroup(runId, attempt) {
    const invocations = invocationsForAttempt(runId, attempt.attemptNumber);
    const expandKey = `${runId}#${attempt.attemptNumber}`;
    const expanded = uiState.expanded.has(expandKey);
    let expandedBody;
    if (!expanded) {
      expandedBody = '';
    } else if (invocations.length > 0) {
      expandedBody = `<ul class="invocation-list">${invocations.map(renderInvocationRow).join('')}</ul>`;
    } else {
      expandedBody = `<p class="invocation-empty">${attemptInvocationEmptyText()}</p>`;
    }
    const summary = attemptSummaryParts(attempt);
    return `<div class="attempt-group">
        <div class="attempt-head">
          <p>Agent 尝试 #${attempt.attemptNumber}${summary ? `<span class="attempt-summary">${summary}</span>` : ''}</p>
          <button type="button" class="attempt-toggle" data-attempt-toggle="${expandKey}" aria-expanded="${expanded}">
            ${expanded ? '收起调用序列' : `展开调用序列（${invocations.length} 次）`}
          </button>
        </div>
        <ol class="timeline">${attempt.eventRows.map(renderEventRow).join('')}</ol>
        ${expandedBody}
      </div>`;
  }

  /** 会话级「其他调用」：账本里存在、但不属于任何已展示尝试的调用行。 */
  function renderOtherInvocationGroup(runId, invocations) {
    if (invocations.length === 0) return '';
    const expandKey = `${runId}#other`;
    const expanded = uiState.expanded.has(expandKey);
    return `<div class="other-invocations">
        <div class="attempt-head">
          <p>其他调用（不属于任何已展示的 Agent 尝试）</p>
          <button type="button" class="attempt-toggle" data-other-toggle="${expandKey}" aria-expanded="${expanded}">
            ${expanded ? '收起调用序列' : `展开调用序列（${invocations.length} 次）`}
          </button>
        </div>
        ${expanded ? `<ul class="invocation-list">${invocations.map(renderInvocationRow).join('')}</ul>` : ''}
      </div>`;
  }

  /**
   * 三类缺口的显式披露：不可用 / 无归属 / 截断各说一件事。
   *
   * 合并成一句会让读的人无法判断该去修哪里；任何一类都只披露，不吞掉时间线其余渲染。
   */
  function renderGapNotice(invocations, unattributedCount, allUnattributed, unmatchedInvocations) {
    const notices = [];
    if (invocations === null || invocations === undefined) {
      notices.push('响应未携带调用明细区（旧记录，或该 PRD 没有关联 Issue）：不显示调用序列，也不按时间窗口猜测归属。');
    } else if (!invocations.available) {
      notices.push('调用明细不可用：调用账本读取失败，展开项不含调用序列；时间线其余部分照常显示。');
    } else if (invocations.rows.length === 0) {
      notices.push('调用账本可读，但没有本 PRD 的调用记录（早于调用观测上线的旧执行）。');
    }
    if (invocations?.truncated) {
      notices.push('调用记录已截断：读取已达账本条数上限，展开项只显示其中一部分。');
    }
    if (unattributedCount > 0) {
      notices.push(allUnattributed
        ? '这些事件都没有执行会话标识（旧记录），已按原顺序平铺展示，未并入任何会话。'
        : `其中 ${unattributedCount} 条事件没有执行会话标识，已按原顺序平铺展示，未并入任何会话。`);
    }
    if (unmatchedInvocations > 0) {
      notices.push(`另有 ${unmatchedInvocations} 条调用记录所属的执行会话在生命周期事件里没有标识，无法归属，因此不显示其调用序列（也不按时间窗口猜测归属）。`);
    }
    if (notices.length === 0) return '';
    return `<div class="gap-notice" role="note">${notices.map((notice) => `<p>${notice}</p>`).join('')}</div>`;
  }

  /** 渲染时间线：披露区 + 会话分组（或旧数据的单层平铺）。 */
  function renderTimeline() {
    eventRegistry = [];
    const scenario = scenarioOf();
    const { sessions, unattributedRows } = groupSessions(scenario.events);
    const sessionRunIds = new Set(sessions.map((session) => session.runId));
    const notice = renderGapNotice(
      scenario.invocations,
      unattributedRows.length,
      sessions.length === 0,
      unmatchedInvocationCount(sessionRunIds),
    );
    if (sessions.length === 0) {
      // 整条时间线都没有会话标识：保持单层平铺，不虚构分组。
      elements.timelineRoot.innerHTML = `${notice}<ol class="timeline">${unattributedRows.map(renderEventRow).join('')}</ol>`;
      return;
    }
    const sessionBlocks = sessions.map((session) => {
      const attemptNumbers = new Set(session.attempts.map((attempt) => attempt.attemptNumber));
      return `<section class="timeline-session">
          <div class="timeline-session-head"><strong>执行会话</strong><code>${session.runId}</code>
            <span>开始：${session.firstOccurredAt}</span></div>
          <ol class="timeline">${session.eventRows.map(renderEventRow).join('')}</ol>
          ${session.attempts.map((attempt) => renderAttemptGroup(session.runId, attempt)).join('')}
          ${renderOtherInvocationGroup(session.runId, otherInvocationsForSession(session.runId, attemptNumbers))}
        </section>`;
    }).join('');
    const unattributedBlock = unattributedRows.length > 0
      ? `<div class="timeline-unattributed"><p>以下事件没有执行会话标识，保持原顺序平铺：</p>
          <ol class="timeline">${unattributedRows.map(renderEventRow).join('')}</ol></div>`
      : '';
    elements.timelineRoot.innerHTML = `${notice}${sessionBlocks}${unattributedBlock}`;
  }

  function renderMetrics() {
    const active = scenarioOf();
    const durations = active.durations;
    const progressNote = active.inProgress ? '计算到当前时刻' : '已结束';
    document.querySelector('#metric-e2e').textContent = formatDuration(durations.end_to_end_seconds);
    document.querySelector('#metric-e2e-note').textContent = progressNote;
    document.querySelector('#metric-active').textContent = formatDuration(durations.active_seconds);
    document.querySelector('#metric-active-note').textContent = `占端到端 ${shareOfTotal(durations.active_seconds, durations.end_to_end_seconds)}`;
    document.querySelector('#metric-waiting').textContent = formatDuration(durations.waiting_seconds);
    document.querySelector('#metric-waiting-note').textContent = `占端到端 ${shareOfTotal(durations.waiting_seconds, durations.end_to_end_seconds)}`;
    document.querySelector('#metric-blocked').textContent = formatDuration(durations.blocked_seconds);
    document.querySelector('#metric-blocked-note').textContent = `占端到端 ${shareOfTotal(durations.blocked_seconds, durations.end_to_end_seconds)}`;
    document.querySelector('#duration-check').textContent =
      `口径：端到端 ${formatDuration(durations.end_to_end_seconds)} = 有效执行 ${formatDuration(durations.active_seconds)} + 等待 ${formatDuration(durations.waiting_seconds)} + 阻塞 ${formatDuration(durations.blocked_seconds)}（互斥，前端不重算）`;
  }

  /** 计算某分类时长占端到端的百分比，用于说明互斥拆分的口径。 */
  function shareOfTotal(partSeconds, totalSeconds) {
    if (!totalSeconds) return '0%';
    return `${Math.round((partSeconds / totalSeconds) * 100)}%`;
  }

  function renderHeader() {
    const active = scenarioOf();
    const status = document.querySelector('#detail-status');
    status.textContent = active.statusLabel;
    status.className = `state ${active.statusClass}`;
    elements.phase.textContent = PHASE_LABELS[active.phase];
    elements.attempts.textContent = active.attempts;
    elements.attemptNote.textContent = active.attemptNote;
    elements.completeness.textContent = active.historyComplete ? '历史完整' : '数据不完整';
    elements.completeness.className = `completeness-pill ${active.historyComplete ? 'is-ok' : 'is-warn'}`;
    elements.warning.hidden = !active.warning;
    elements.warning.textContent = active.warning;
  }

  function renderStats() {
    const stats = STATS_FIXTURE;
    document.querySelector('#stats-completed').textContent = String(stats.completed_runs);
    document.querySelector('#stats-avg').textContent = formatDuration(stats.average_end_to_end_seconds);
    document.querySelector('#stats-median').textContent = formatDuration(stats.median_end_to_end_seconds);
    document.querySelector('#stats-p90').textContent = formatDuration(stats.p90_end_to_end_seconds);
    document.querySelector('#stats-blocked').textContent = formatDuration(stats.average_blocked_seconds);
    document.querySelector('#stats-bottleneck').innerHTML =
      `<li><span>${stats.bottleneck_phase}</span><strong>${formatDuration(stats.bottleneck_phase_seconds)}</strong></li>`;
    elements.unlinkedNote.textContent =
      `另有 ${stats.unlinked_run_count} 条旧运行记录只有 Issue 编号、无法可靠关联 PRD，已标记为「未关联 PRD」并从完成分位数中排除；最近运行列表仍可查看它们。`;

    const active = scenarioOf();
    const searchQuery = elements.runsSearch.value.trim().toLowerCase();
    const visibleRuns = stats.runs.map((run) => run.dynamic
        ? { name: 'PRD 生命周期观测与执行分析', issue: 417, status: active.statusClass, statusLabel: active.statusLabel,
            start: '09-21 13:42', end: active.inProgress ? `当前阶段：${PHASE_LABELS[active.phase]}` : '已结束',
            active: formatDuration(active.durations.active_seconds), waiting: formatDuration(active.durations.waiting_seconds),
            total: formatDuration(active.durations.end_to_end_seconds), retry: 1, link: true, incomplete: !active.historyComplete }
        : run).filter((run) => !searchQuery || `${run.name} ${run.issue} ${run.statusLabel}`.toLowerCase().includes(searchQuery));
    elements.runsBody.innerHTML = visibleRuns.length ? visibleRuns.map((row) => {
      const nameCell = row.link
        ? `<button class="table-link hotspot-target" type="button" data-view-link="roadmap">${row.name} <small>#${row.issue}</small></button>`
        : `<span>${row.name} <small>#${row.issue}</small></span>`;
      const statusCell = row.incomplete
        ? `<span class="state ${row.status}">${row.statusLabel}</span><small class="incomplete-note">数据不完整</small>`
        : `<span class="state ${row.status}">${row.statusLabel}</span>`;
      return `<tr><td>${nameCell}</td><td>${statusCell}</td><td>${row.start}</td><td>${row.end}</td><td>${row.active}</td><td>${row.waiting}</td><td><strong>${row.total}</strong></td><td>${row.retry}</td></tr>`;
    }).join('') : '<tr><td colspan="8">没有匹配的记录。</td></tr>';
  }

  function showView(view) {
    uiState.view = view;
    document.querySelectorAll('[data-view]').forEach((page) => page.classList.toggle('is-visible', page.dataset.view === view));
    document.querySelectorAll('.product-sidebar [data-view-link]').forEach((link) => link.classList.toggle('is-active', link.dataset.viewLink === view));
    const viewTitles = { dashboard: 'Dashboard', evaluations: '模型评测', 'evaluation-baseline': '评测基线', stats: 'Stats', roadmap: 'Backlog' };
    elements.pageTitle.textContent = viewTitles[view] ?? 'Dashboard';
    if (view === 'stats') renderStats();
    if (view === 'dashboard') window.renderRuntimeDashboard?.();
    if (view === 'evaluations') window.renderRuntimeEvaluation?.();
    if (view === 'evaluation-baseline') window.renderEvaluationBaseline?.();
    window.location.hash = view;
  }

  /** 事件抽屉：展示原始 detail，被折叠的事件同样可点开，账本内容零改写。 */
  function openInspector(entry) {
    if (!entry) return;
    const badge = badgeOf(entry);
    document.querySelector('#inspector-title').textContent = EVENT_TYPE_LABELS[entry.eventType] ?? entry.eventType;
    document.querySelector('#inspector-detail').textContent = entry.note ?? '';
    document.querySelector('#inspector-meta').innerHTML = `
      <div><dt>事件类型</dt><dd><code>${entry.eventType}</code></dd></div>
      <div><dt>徽章口径</dt><dd><span class="chip ${badge.variant}">${badge.label}</span></dd></div>
      <div><dt>发生时间</dt><dd>2026-09-21 ${entry.time} +08:00</dd></div>
      <div><dt>执行者</dt><dd>${entry.actor}</dd></div>
      <div><dt>所属阶段</dt><dd>${PHASE_LABELS[entry.phase] ?? entry.phase}（<code>${entry.phase}</code>）</dd></div>
      <div><dt>所属会话</dt><dd>${sessionKeyOf(entry) ? `<code>${sessionKeyOf(entry)}</code>` : '—（无会话标识，平铺展示）'}</dd></div>
      <div><dt>关联 run</dt><dd><code>${RUN_ID}</code></dd></div>
      <div><dt>PRD 路径</dt><dd><code>${PRD_PATH}</code></dd></div>
      <div><dt>原始 detail</dt><dd><code>${JSON.stringify(entry.detail)}</code></dd></div>`;
    elements.inspector.hidden = false;
  }

  function renderAll() {
    renderHeader();
    renderMetrics();
    renderTimeline();
    if (uiState.view === 'stats') renderStats();
  }

  function selectScenario(scenarioKey) {
    uiState.scenario = scenarioKey;
    uiState.expanded.clear();
    document.querySelectorAll('[data-scenario]').forEach((button) => {
      const isActive = button.dataset.scenario === scenarioKey;
      button.classList.toggle('is-active', isActive);
      button.setAttribute('aria-pressed', String(isActive));
    });
    elements.inspector.hidden = true;
    renderAll();
  }

  document.addEventListener('click', (event) => {
    const viewLink = event.target.closest('[data-view-link]');
    if (viewLink) { event.preventDefault(); elements.inspector.hidden = true; showView(viewLink.dataset.viewLink); return; }
    const scenarioButton = event.target.closest('[data-scenario]');
    if (scenarioButton) { selectScenario(scenarioButton.dataset.scenario); return; }
    const expandButton = event.target.closest('[data-attempt-toggle],[data-other-toggle]');
    if (expandButton) {
      const expandKey = expandButton.dataset.attemptToggle ?? expandButton.dataset.otherToggle;
      if (uiState.expanded.has(expandKey)) uiState.expanded.delete(expandKey);
      else uiState.expanded.add(expandKey);
      renderTimeline();
      return;
    }
    const eventRow = event.target.closest('[data-event-ref]');
    if (eventRow) openInspector(eventRegistry[Number(eventRow.dataset.eventRef)]);
    if (event.target.closest('[data-close-inspector]')) elements.inspector.hidden = true;
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const eventRow = event.target.closest('[data-event-ref]');
    if (eventRow) { event.preventDefault(); openInspector(eventRegistry[Number(eventRow.dataset.eventRef)]); }
  });

  // 底部原型 Dock 的「重置」会派发该事件：回到初始场景与 Backlog 视图。
  document.addEventListener('prototype:reset', () => {
    selectScenario('normal');
    showView('dashboard');
  });

  document.addEventListener('keyup', (event) => {
    if (event.key === 'Escape') elements.inspector.hidden = true;
  });

  elements.runsSearch.addEventListener('input', renderStats);
  window.addEventListener('hashchange', () => {
    const hashView = window.location.hash.slice(1);
    const requestedView = ['dashboard', 'evaluations', 'evaluation-baseline', 'stats', 'roadmap'].includes(hashView) ? hashView : 'dashboard';
    if (requestedView !== uiState.view) showView(requestedView);
  });

  renderAll();
  const initialView = ['dashboard', 'evaluations', 'evaluation-baseline', 'stats', 'roadmap'].includes(window.location.hash.slice(1))
    ? window.location.hash.slice(1)
    : 'dashboard';
  showView(initialView);
})();
