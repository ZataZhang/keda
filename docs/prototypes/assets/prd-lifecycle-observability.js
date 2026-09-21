/*
 * PRD 生命周期观测与执行分析 · 交互原型状态模型
 *
 * 说明：本文件是**静态 fixture 驱动**的原型脚本，不含任何随机数、定时器或网络请求，
 * 也不接入真实 API / SQLite。所有数字与事件都是为了让评审确认信息层级与耗时口径而预先写死的，
 * 不代表生产环境已经具备这些能力。fixture 字段命名对齐后端只读契约
 * （lifecycle detail 与 console stats），便于实施时按同一形状对接。
 */
(() => {
  // 稳定 run id：同一 PRD 的历史跨重试共享它，Issue 编号只是外部关联。
  const RUN_ID = 'prdrun_01K5R4Z7Q9';
  const PRD_PATH = 'tasks/pending/P1-FEAT-20260921-161621-prd-lifecycle-observability.md';

  // current_phase 闭集 → 中文展示名（与后端枚举一一对应）。
  const PHASE_LABELS = {
    none: '未开始', queued: '排队中', executing: '执行中', validating: '验证中',
    reviewing: '审阅中', merging: '合并中', blocked: '阻塞中', failed: '失败', completed: '已完成',
  };

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
      { name: 'Roadmap PRD 控制与验收证据', issue: 402, status: 'done', statusLabel: '已归档', start: '09-20 09:18', end: '09-20 12:02', active: '2h 09m', waiting: '35m', total: '2h 44m', retry: 0, link: true },
      { name: '生命周期 Agent 矩阵', issue: 395, status: 'done', statusLabel: '已归档', start: '09-18 10:11', end: '09-18 16:39', active: '4h 51m', waiting: '1h 37m', total: '6h 28m', retry: 2, link: true },
      { name: 'Dashboard 快照同步', issue: 371, status: 'failed', statusLabel: '失败', start: '09-16 14:08', end: '验证失败', active: '1h 06m', waiting: '11m', total: '1h 17m', retry: 3, link: true },
    ],
  };

  // 三套稳定场景：正常成功路径、失败重试路径、观测写入降级路径。
  const SCENARIOS = {
    normal: {
      statusLabel: '进行中', statusClass: 'running', phase: 'reviewing',
      inProgress: true, outcome: null, historyComplete: true, warning: '',
      durations: { end_to_end_seconds: 8280, active_seconds: 6120, waiting_seconds: 2160, blocked_seconds: 0 },
      attempts: '3 次', attemptNote: '1 次失败后自动恢复',
      events: [
        { time: '13:42', type: 'wait', title: '进入执行队列', detail: '由 Roadmap 手动启动，core 创建稳定 run 标识并追加首个事件。', duration: '等待 12m', actor: 'operator', eventType: 'queued', phase: 'queued', reason: '操作者在 Roadmap 点击「单个开始」' },
        { time: '13:54', type: 'success', title: 'Agent 开始实现', detail: 'Issue #417 进入 agent/running，首次 attempt 使用 codex。', duration: '执行 48m', actor: 'codex', eventType: 'started', phase: 'executing', reason: 'runner 领取到可处理 Issue' },
        { time: '14:42', type: 'failure', title: '首次验证未通过', detail: '真实入口断言失败；失败事件被保留，后续成功不会覆盖它。', duration: '7m', actor: 'verifier', eventType: 'validation_failed', phase: 'validating', reason: 'Playwright 真实入口断言失败 1 项' },
        { time: '14:49', type: 'success', title: '失败后自动恢复', detail: '复用同一 PRD run 新建 attempt，重新收集最终代码树证据。', duration: '执行 35m', actor: 'codex', eventType: 'recovered', phase: 'executing', reason: '同一 run 内重试，不新建 run' },
        { time: '15:24', type: 'success', title: '验证通过', detail: '证据门禁通过，等待独立 verifier 返回审阅结论。', duration: '7m', actor: 'verifier', eventType: 'validation_passed', phase: 'validating', reason: '全部校验项通过' },
        { time: '15:31', type: 'wait', title: '等待独立审查', detail: '机器证据已齐，正在等待 verifier 返回结论（尚未结束，不伪造结束时间）。', duration: '进行中', actor: 'verifier', eventType: 'review_started', phase: 'reviewing', reason: '等待外部审阅结果' },
      ],
    },
    failure: {
      statusLabel: '阻塞中', statusClass: 'blocked', phase: 'blocked',
      inProgress: true, outcome: null, historyComplete: true, warning: '',
      durations: { end_to_end_seconds: 9060, active_seconds: 5040, waiting_seconds: 1620, blocked_seconds: 2400 },
      attempts: '4 次', attemptNote: '3 次失败，1 次人工重试',
      events: [
        { time: '13:42', type: 'wait', title: '进入执行队列', detail: '由 Roadmap 手动启动，创建稳定 PRD run 标识。', duration: '等待 12m', actor: 'operator', eventType: 'queued', phase: 'queued', reason: '操作者在 Roadmap 点击「单个开始」' },
        { time: '13:54', type: 'success', title: 'Agent 开始实现', detail: 'Issue #417 进入 agent/running，首次 attempt 使用 codex。', duration: '执行 48m', actor: 'codex', eventType: 'started', phase: 'executing', reason: 'runner 领取到可处理 Issue' },
        { time: '14:42', type: 'failure', title: '连续验证失败', detail: '三次 attempt 均未跨过真实浏览器入口；失败事件与耗时全部保留。', duration: '29m', actor: 'verifier', eventType: 'validation_failed', phase: 'validating', reason: '真实入口断言连续 3 次失败' },
        { time: '15:11', type: 'success', title: '人工触发重试', detail: '从失败点新建 attempt，不覆盖旧失败事件与耗时。', duration: '执行 21m', actor: 'operator', eventType: 'retry', phase: 'executing', reason: '操作者关闭代码缺陷后手动重试' },
        { time: '15:32', type: 'failure', title: '进入阻塞', detail: '依赖的 CI 服务不可用；阻塞时间单独累计，不计入有效执行时间。', duration: '阻塞中', actor: 'system', eventType: 'blocked', phase: 'blocked', reason: '上游 CI 服务不可用，等待恢复' },
      ],
    },
    degraded: {
      statusLabel: '进行中', statusClass: 'running', phase: 'validating',
      inProgress: true, outcome: null, historyComplete: false,
      warning: '生命周期记录不完整：一次事件写入失败，时间线存在缺口。runner 主流程未被阻断，日志中已保留可诊断错误。',
      durations: { end_to_end_seconds: 4800, active_seconds: 3600, waiting_seconds: 1200, blocked_seconds: 0 },
      attempts: '2 次', attemptNote: '观测写入失败 1 次',
      events: [
        { time: '13:42', type: 'wait', title: '进入执行队列', detail: '由 Roadmap 手动启动，创建稳定 PRD run 标识。', duration: '等待 12m', actor: 'operator', eventType: 'queued', phase: 'queued', reason: '操作者在 Roadmap 点击「单个开始」' },
        { time: '13:54', type: 'success', title: 'Agent 开始实现', detail: 'Issue #417 进入 agent/running。', duration: '执行 48m', actor: 'codex', eventType: 'started', phase: 'executing', reason: 'runner 领取到可处理 Issue' },
        { time: '14:42', type: 'failure', title: '生命周期事件写入失败', detail: '旁路观测存储异常，事件未落库；runner 继续执行，history_complete 置为 false。', duration: '—', actor: 'system', eventType: 'failed', phase: 'executing', reason: '观测存储写入异常（不阻断主流程）' },
        { time: '15:41', type: 'success', title: '实现完成（事件缺失）', detail: '主流程已完成实现并进入验证，但对应事件缺失，页面据此显示数据不完整告警。', duration: '执行 47m', actor: 'codex', eventType: 'implementation_completed', phase: 'validating', reason: '主流程继续，观测缺口已记录' },
      ],
    },
  };

  const EVENT_MARKERS = { failure: '!', wait: '◷', success: '✓', info: '•' };
  const uiState = { view: 'roadmap', scenario: 'normal' };
  const elements = {
    timeline: document.querySelector('#timeline'),
    inspector: document.querySelector('#event-inspector'),
    pageTitle: document.querySelector('#page-title'),
    phase: document.querySelector('#phase-value'),
    attempts: document.querySelector('#attempt-value'),
    attemptNote: document.querySelector('#attempt-note'),
    completeness: document.querySelector('#completeness-pill'),
    warning: document.querySelector('#data-warning'),
    runsBody: document.querySelector('#runs-body'),
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

  /** 计算某分类时长占端到端的百分比，用于说明互斥拆分的口径。 */
  function shareOfTotal(partSeconds, totalSeconds) {
    if (!totalSeconds) return '0%';
    return `${Math.round((partSeconds / totalSeconds) * 100)}%`;
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

  function renderTimeline() {
    elements.timeline.innerHTML = scenarioOf().events.map((entry, index) => `
      <li class="timeline-event hotspot-target${entry.type && entry.type !== 'success' ? ` is-${entry.type}` : ''}" tabindex="0" role="button" data-event-index="${index}" aria-label="查看事件：${entry.title}">
        <time>${entry.time}</time><span class="event-marker">${EVENT_MARKERS[entry.type] || '•'}</span>
        <span class="timeline-copy"><strong>${entry.title}</strong><small>${entry.detail}</small></span><span class="event-duration">${entry.duration}</span>
      </li>`).join('');
  }

  function renderStats() {
    const stats = STATS_FIXTURE;
    document.querySelector('#stats-completed').textContent = String(stats.completed_runs);
    document.querySelector('#stats-avg').textContent = formatDuration(stats.average_end_to_end_seconds);
    document.querySelector('#stats-median').textContent = formatDuration(stats.median_end_to_end_seconds);
    document.querySelector('#stats-p90').textContent = formatDuration(stats.p90_end_to_end_seconds);
    document.querySelector('#stats-blocked').textContent = formatDuration(stats.average_blocked_seconds);
    document.querySelector('#stats-bottleneck').innerHTML =
      `<span>${stats.bottleneck_phase}</span><strong>${formatDuration(stats.bottleneck_phase_seconds)}</strong>`;
    elements.unlinkedNote.textContent =
      `另有 ${stats.unlinked_run_count} 条旧运行记录只有 Issue 编号、无法可靠关联 PRD，已标记为「未关联 PRD」并从完成分位数中排除；最近运行列表仍可查看它们。`;

    const active = scenarioOf();
    elements.runsBody.innerHTML = stats.runs.map((run) => {
      const row = run.dynamic
        ? { name: 'PRD 生命周期观测与执行分析', issue: 417, status: active.statusClass, statusLabel: active.statusLabel,
            start: '09-21 13:42', end: active.inProgress ? `当前阶段：${PHASE_LABELS[active.phase]}` : '已结束',
            active: formatDuration(active.durations.active_seconds), waiting: formatDuration(active.durations.waiting_seconds),
            total: formatDuration(active.durations.end_to_end_seconds), retry: 1, link: true, incomplete: !active.historyComplete }
        : run;
      const nameCell = row.link
        ? `<button class="table-link hotspot-target" type="button" data-view-link="roadmap">${row.name} <small>#${row.issue}</small></button>`
        : `${row.name} <small>#${row.issue}</small>`;
      const statusCell = row.incomplete
        ? `<span class="state ${row.status}">${row.statusLabel}</span><small class="incomplete-note">数据不完整</small>`
        : `<span class="state ${row.status}">${row.statusLabel}</span>`;
      return `<tr><td>${nameCell}</td><td>${statusCell}</td><td>${row.start}</td><td>${row.end}</td><td>${row.active}</td><td>${row.waiting}</td><td><strong>${row.total}</strong></td><td>${row.retry}</td></tr>`;
    }).join('') + `
      <tr class="legacy-row"><td><span class="legacy-name">旧运行记录 <small>#288</small></span></td><td><span class="state legacy">未关联 PRD</span></td><td>09-10 08:20</td><td>已结束</td><td>52m</td><td>9m</td><td><strong>1h 01m</strong></td><td>0</td></tr>`;
  }

  function showView(view) {
    uiState.view = view;
    document.querySelectorAll('[data-view]').forEach((page) => page.classList.toggle('is-visible', page.dataset.view === view));
    document.querySelectorAll('.product-sidebar [data-view-link]').forEach((link) => link.classList.toggle('is-active', link.dataset.viewLink === view));
    elements.pageTitle.textContent = view === 'stats' ? '统计' : 'Roadmap';
    if (view === 'stats') renderStats();
    window.location.hash = view;
  }

  function openInspector(index) {
    const entry = scenarioOf().events[index];
    if (!entry) return;
    document.querySelector('#inspector-title').textContent = entry.title;
    document.querySelector('#inspector-detail').textContent = entry.detail;
    document.querySelector('#inspector-meta').innerHTML = `
      <div><dt>事件类型</dt><dd><code>${entry.eventType}</code></dd></div>
      <div><dt>发生时间</dt><dd>2026-09-21 ${entry.time}:00 +08:00</dd></div>
      <div><dt>执行者</dt><dd>${entry.actor}</dd></div>
      <div><dt>原因</dt><dd>${entry.reason}</dd></div>
      <div><dt>所属阶段</dt><dd>${PHASE_LABELS[entry.phase]}（<code>${entry.phase}</code>）</dd></div>
      <div><dt>关联 run</dt><dd><code>${RUN_ID}</code></dd></div>
      <div><dt>PRD 路径</dt><dd><code>${PRD_PATH}</code></dd></div>`;
    elements.inspector.hidden = false;
  }

  function renderAll() {
    renderHeader();
    renderMetrics();
    renderTimeline();
    if (uiState.view === 'stats') renderStats();
  }

  document.addEventListener('click', (event) => {
    const viewLink = event.target.closest('[data-view-link]');
    if (viewLink) { event.preventDefault(); elements.inspector.hidden = true; showView(viewLink.dataset.viewLink); return; }
    const scenarioButton = event.target.closest('[data-scenario]');
    if (scenarioButton) {
      uiState.scenario = scenarioButton.dataset.scenario;
      document.querySelectorAll('[data-scenario]').forEach((button) => button.classList.toggle('is-active', button === scenarioButton));
      elements.inspector.hidden = true;
      renderAll();
      return;
    }
    const eventRow = event.target.closest('[data-event-index]');
    if (eventRow) openInspector(Number(eventRow.dataset.eventIndex));
    if (event.target.closest('[data-close-inspector]')) elements.inspector.hidden = true;
  });

  document.addEventListener('keydown', (event) => {
    const eventRow = event.target.closest('[data-event-index]');
    if (eventRow && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openInspector(Number(eventRow.dataset.eventIndex)); }
    if (event.key === 'Escape') elements.inspector.hidden = true;
  });

  // 底部原型 Dock 的「重置」会派发该事件：回到初始场景与 Roadmap 视图。
  document.addEventListener('prototype:reset', () => {
    uiState.scenario = 'normal';
    document.querySelectorAll('[data-scenario]').forEach((button) => button.classList.toggle('is-active', button.dataset.scenario === 'normal'));
    elements.inspector.hidden = true;
    renderAll();
    showView('roadmap');
  });

  renderAll();
  showView(window.location.hash === '#stats' ? 'stats' : 'roadmap');
})();
