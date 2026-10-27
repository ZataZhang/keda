(() => {
  const prototypeRegistry = [
    {
      id: 'tailscale-machine-management', title: 'Keda 多机连接与远程文档', project: 'keda', module: 'Console / 机器管理', system: 'Keda Console',
      form: 'code-native', version: 'v1.0', updatedAt: '2026-10-18', availability: 'available', validationLevel: '交互原型',
      primaryFlow: '从机器管理开始添加电脑，查看 Tailscale 入网、系统远程登录和授权目录三个步骤；再浏览远程文档或管理指定机器上的 Runner',
      description: '基于现有 Keda Console 风格的多机连接与远程文档目标态。固定数据演示首次连接中 Agent 与人的分工、只读目录授权、远程文件预览和 Runner 启停确认；不连接真实设备、Tailscale、SSH、文件系统或 API。',
      preview: '', entry: './tailscale-machine-management/index.html', source: './tailscale-machine-management/prototype.md', provenance: [],
      relationships: [],
    },
    {
      id: 'blocked-draft-pr-surface', title: '失败上下文交接与失败 Draft PR', project: 'keda', module: 'Agent Runner / 跨 claim 交接',
      form: 'image-state', version: 'v2.0', updatedAt: '2026-09-23', availability: 'available', validationLevel: '概念原型',
      primaryFlow: '看两层表面：面板 A 是带 iar:failure-context marker 的 Issue 交接评论（下一轮 agent 读它），面板 B 是同一 payload 渲染出的 Draft PR 正文（人读）',
      description: '复刻 GitHub 上的两层呈现：交接评论含快照性质、卡在哪、verifier 判定摘要、尝试历史摘要、缺失呈递物与快照 SHA；PR 正文同源，且不可合并归因给既有 validation/verifier-passed 门禁。无新标签、无状态码、无判定器。',
      preview: './assets/blocked-draft-pr-01-failure-context-comment.png', entry: './blocked-draft-pr-surface.html', source: './blocked-draft-pr-surface/',
      provenance: ['./assets/blocked-draft-pr-01-failure-context-comment.source/', './assets/blocked-draft-pr-02-draft-pr-body.source/'],
      relationships: [{ id: 'prd-lifecycle-observability', label: '验证失败后的生命周期与恢复时间线' }],
    },
    {
      id: 'verifier-human-takeover', title: 'Verifier 运行中的单 Issue 人工接管', project: 'keda', module: 'Agent Runner / Dashboard',
      system: 'Keda Console', form: 'code-native', version: 'v1.0', updatedAt: '2026-10-11', availability: 'available', validationLevel: '交互原型',
      primaryFlow: '从 Dashboard 的 Issue 详情确认接管当前 verifier，查看处理中与交接完成；也可切换 verifier 已结束、HEAD 漂移和停止失败状态',
      description: '基于真实 Dashboard 与 IssueDetail 页面结构的可点击流程演示。用静态数据呈递只停止目标 Issue verifier、保留 Draft PR、取消不产生验证结论的方案；不连接 API 或进程。',
      preview: '', entry: './verifier-human-takeover.html', source: './verifier-human-takeover.md', provenance: [],
      relationships: [{ id: 'blocked-draft-pr-surface', label: '查看验证失败后的 Issue 交接与 Draft PR' }],
    },
    {
      id: 'prd-lifecycle-observability', title: 'Console 运行时看板与模型评测', project: 'keda', module: 'Agent Runner / Dashboard',
      system: 'Keda Console', form: 'code-native', version: 'v3.0', updatedAt: '2026-10-12', availability: 'available', validationLevel: '交互原型',
      primaryFlow: '默认查看运行时任务卡片与详情日志，折叠工具调用；打开四任务桌面悬浮窗预览；进入模型评测配置、查看逐维评分、评审分歧与外发授权流程；也可回到 Backlog 生命周期详情和 Stats',
      description: '静态 fixture 驱动的 Console 目标态交互原型。Dashboard 卡片用状态颜色与文字呈现 Issue 运行阶段；右侧展示日志和可折叠结构化调用；桌面页用紧凑四任务布局展示改动树触达比例，但不模拟跨应用置顶；评测页展示固定样本对比、多评审模型逐维评分和确切 payload 预览，拒绝与确认都不触发真实网络请求。原型不连接 API、SQLite、Agent 或模型服务。',
      preview: './assets/runtime-board-preview.png', entry: './prd-lifecycle-observability.html#dashboard', source: './prd-lifecycle-observability.md', provenance: ['./assets/runtime-board-preview.source/', './assets/runtime-widget-preview.source/', './assets/runtime-evaluation-preview.source/', './assets/runtime-consent-preview.source/'],
      relationships: [
        { id: 'roadmap-cicd-repair', label: '继续查看 CI/CD 检查与自动修复轮次' },
        { id: 'roadmap-controls-evidence', label: '查看同一 Backlog 画布上的控制与验收证据入口' },
        { id: 'lifecycle-agent-matrix', label: '查看生命周期执行器与模型设置' },
        { id: 'blocked-draft-pr-surface', label: '查看验证失败后的 Issue 交接与 Draft PR' },
        { id: 'verifier-human-takeover', label: '查看 Dashboard 上的 verifier 接管流程' },
        { id: 'm13-keda-vs-claude-paired-evaluation', label: '查看 Keda 工作流与 Claude 单次执行的配对结果目标图' },
      ],
    },
    {
      id: 'm13-keda-vs-claude-paired-evaluation', title: 'Keda 工作流与 Claude 单次执行配对评测', project: 'keda', module: 'Agent Runner / Model Evaluation',
      system: 'Keda Console', form: 'image-state', version: 'v1.0', updatedAt: '2026-10-19', availability: 'available', validationLevel: '设计意图（静态目标图）',
      primaryFlow: '在同一题详情里并列查看 Claude 单次执行与 Keda 工作流的重复通过情况、阶段、耗时和调用次数',
      description: 'M13 配对评测详情页的单张静态目标图，展示 direct 与 Keda 两组重复结果。示例数据只表达目标信息结构；没有交互，不连接 API、SQLite 或 Agent，也不是生产页面验证证据。',
      preview: './assets/keda-vs-claude-paired-evaluation.svg', entry: './assets/keda-vs-claude-paired-evaluation.svg', source: './assets/keda-vs-claude-paired-evaluation.source.md',
      provenance: ['./assets/keda-vs-claude-paired-evaluation.source.md'],
      relationships: [{ id: 'prd-lifecycle-observability', label: '查看现有 Console 模型评测入口与页面结构' }],
    },
    {
      id: 'roadmap-cicd-repair', title: 'CI/CD 监控与自动修复', project: 'keda', module: 'Backlog',
      form: 'image-state', version: 'v1.0', updatedAt: '2026-09-16', availability: 'available', validationLevel: '概念原型',
      primaryFlow: '查看全局默认与单 PRD 覆盖的界面方案', description: 'Backlog 页面中 CI/CD 等待、问题呈现与自动修复控制的高保真界面草图。',
      preview: './assets/roadmap-prd-cicd-01-waiting.png', entry: './roadmap-prd-cicd-auto-repair.html', source: './roadmap-prd-cicd-auto-repair/',
      provenance: [
        './assets/roadmap-prd-cicd-auto-repair.prompt/',
        './assets/roadmap-prd-cicd-01-waiting.prompt/',
        './assets/roadmap-prd-cicd-02-repairing.prompt/',
        './assets/roadmap-prd-cicd-03-rechecking.prompt/',
        './assets/roadmap-prd-cicd-04-passed.prompt/',
      ],
      relationships: [
        { id: 'prd-lifecycle-observability', label: '在同一 PRD 流程查看生命周期时间线与仓库统计' },
        { id: 'roadmap-controls-evidence', label: '查看 Backlog 控制、验收证据与 Autopilot 草图' },
      ],
    },
    {
      id: 'roadmap-controls-evidence', title: '单 PRD 控制与验收证据', project: 'keda', module: 'Backlog',
      form: 'image-state', version: 'v2.0', updatedAt: '2026-10-10', availability: 'available', validationLevel: '概念原型',
      primaryFlow: '在单 PRD 详情头部常驻「清单 / 影响树 / 证据」三个弱信号读数，进入验收证据标签后按计划条目分组浏览产出与未合并来源',
      description: '单 PRD 控制、验收证据与 Autopilot 草图。v2.0 把详情升级为交付进度信号：头部三读数（清单勾选、影响树 ~触达/可判定 ?无法判定、证据 已产出/计划）以弱信号口径呈现，确认无数据用 `-`、读取不可用单独披露；证据标签从平铺列表升级为按验证条目分组、标注「未合并」来源与「待产出」占位。运行中读数来自执行工作区（主仓库兜底）、归档为终态事实来源。三种关键状态（运行中 / 已归档 / 无计划降级）各有目标图。',
      preview: './assets/roadmap-prd-controls-evidence-autopilot.png', entry: './assets/roadmap-prd-controls-evidence-autopilot.png', source: './roadmap-prd-controls-evidence-autopilot/',
      provenance: [
        './assets/roadmap-prd-controls-evidence-autopilot.prompt/',
        './assets/roadmap-prd-controls-evidence-autopilot-progress-running.png',
        './assets/roadmap-prd-controls-evidence-autopilot-progress-archived.png',
        './assets/roadmap-prd-controls-evidence-autopilot-progress-degraded.png',
      ],
      relationships: [
        { id: 'roadmap-cicd-repair', label: '查看同一 Backlog 页面上的 CI/CD 状态与自动修复' },
        { id: 'prd-lifecycle-observability', label: '查看 PRD 执行时间线与统计' },
      ],
    },
    {
      id: 'lifecycle-agent-matrix', title: '生命周期与执行器统一设置', project: 'keda', module: 'Console 设置', system: 'Keda Console',
      form: 'code-native', version: 'v3.1', updatedAt: '2026-10-09', availability: 'available', validationLevel: '交互原型',
      primaryFlow: '从 Settings 的统一设置入口或 Backlog 仓库齿轮进入同一页面；定义模型预设、查看九阶段 Agent / 模型 / 推理深度和来源，并配置执行器回退候选队列与写入预览',
      description: '在既有 lifecycle-agent-matrix 原型内将生命周期矩阵、模型预设和执行器回退顺序放进同一设置页，并用区块导航定位三类设置。矩阵调整为四列，放大字段并将预设改为双列卡片；Settings 仅保留 Agent 标签与页面入口。复用真实 frontend-public 截图作产品外壳，交互覆盖全局/仓库范围、阶段绑定、预设编辑、新建预设、可重复执行器的候选排序（不同预设分别成行）和写入预览；保存分别预览生命周期范围配置与全局 Runner 的 TOML 写入；Backlog 仓库齿轮进入同一设置页并预选仓库，PRD 覆盖流程仍保留。',
      preview: './assets/lifecycle-agent-matrix/preview-lifecycle-settings.png', entry: './lifecycle-agent-matrix.html', source: './lifecycle-agent-matrix/',
      provenance: ['./assets/lifecycle-agent-matrix/preview-lifecycle-settings.source/'],
      relationships: [{ id: 'prd-lifecycle-observability', label: '从配置页转到 PRD 执行过程与仓库统计' }],
    },
    {
      id: 'kc-terminal-agent-preview', title: 'KC 终端执行器与按需项目预览', project: 'keda', module: 'Developer Experience / CLI',
      form: 'image-state', version: 'v1.0', updatedAt: '2026-10-09', availability: 'available', validationLevel: '概念原型',
      primaryFlow: '输入 kc 启动设置中选定的 Codex / Claude 原生终端执行器；对话请求打开项目页面后，终端打印本机预览 URL',
      description: '基于 agentic entry PRD 的静态终端概念图：对话留在执行器原生 TUI，项目预览只按请求启动并把 loopback URL 返回给用户；没有 KC 网页聊天，也不自动打开浏览器。',
      preview: './assets/kc-terminal-agent-preview.png', entry: './assets/kc-terminal-agent-preview.png', source: './kc-agent-terminal-preview/',
      provenance: ['./assets/kc-terminal-agent-preview.prompt/'],
    },
  ];

  const formLabels = { 'image-state': '图片原型', 'code-native': '交互原型' };
  const availabilityLabels = { available: '可用', archived: '已归档' };
  const state = { query: '', project: 'all', module: 'all', system: 'all', form: 'all', availability: 'all', selectedId: prototypeRegistry[0].id };
  const elements = {
    list: document.querySelector('#prototype-list'), detail: document.querySelector('#prototype-detail'), empty: document.querySelector('#empty-state'),
    visibleCount: document.querySelector('#visible-count'), totalCount: document.querySelector('[data-total-count]'), search: document.querySelector('#prototype-search'),
    project: document.querySelector('#project-filter'), module: document.querySelector('#module-filter'), system: document.querySelector('#system-filter'), form: document.querySelector('#form-filter'),
    availability: document.querySelector('#availability-filter'), clear: document.querySelector('#clear-filters'), navItems: [...document.querySelectorAll('[data-nav-filter]')],
  };

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
  const uniqueValues = (key) => [...new Set(prototypeRegistry.map((prototype) => prototype[key]))].sort();
  const appendOptions = (select, values) => values.forEach((value) => select.add(new Option(value, value)));
  const matchesFilters = (prototype) => {
    const searchableText = `${prototype.title} ${prototype.description} ${prototype.project} ${prototype.module}`.toLowerCase();
    return (!state.query || searchableText.includes(state.query))
      && (state.project === 'all' || prototype.project === state.project)
      && (state.module === 'all' || prototype.module === state.module)
      && (state.system === 'all' || prototype.system === state.system)
      && (state.form === 'all' || prototype.form === state.form)
      && (state.availability === 'all' || prototype.availability === state.availability);
  };
  const previewMarkup = (prototype) => prototype.preview
    ? `<img class="catalog-thumb" src="${escapeHtml(prototype.preview)}" alt="${escapeHtml(prototype.title)}预览" />`
    : '<span class="catalog-thumb catalog-thumb-placeholder" aria-hidden="true">HTML</span>';

  const renderDetail = () => {
    const prototype = prototypeRegistry.find((candidate) => candidate.id === state.selectedId);
    if (!prototype) {
      elements.detail.innerHTML = '<div class="empty-state"><strong>请选择一个原型</strong><span>详情将在这里显示。</span></div>';
      return;
    }
    const canOpenPrototype = prototype.availability === 'available' && Boolean(prototype.entry);
    const largePreview = prototype.preview
      ? `<img src="${escapeHtml(prototype.preview)}" alt="${escapeHtml(prototype.title)}大图预览" />`
      : '<span class="detail-placeholder">Interactive HTML</span>';
    const relationshipMarkup = (prototype.relationships ?? []).map((relationship) => {
      const relatedPrototype = prototypeRegistry.find((candidate) => candidate.id === relationship.id);
      if (!relatedPrototype) return '';
      const canOpenRelated = relatedPrototype.availability === 'available' && Boolean(relatedPrototype.entry);
      return `<li>${canOpenRelated
        ? `<a href="${escapeHtml(relatedPrototype.entry)}" target="_blank" rel="noreferrer">${escapeHtml(relatedPrototype.title)} ↗</a>`
        : `<span class="related-unavailable">${escapeHtml(relatedPrototype.title)}（暂不可用）</span>`}<small>${escapeHtml(relationship.label)}</small></li>`;
    }).join('');
    const provenanceMarkup = (prototype.provenance ?? []).map((sidecarPath) => {
      const sidecarName = sidecarPath.endsWith('.md')
        ? sidecarPath.split('/').pop()
        : sidecarPath.replace(/\/$/, '').split('/').pop() + '.md';
      return `<a href="${escapeHtml(sidecarPath)}" target="_blank" rel="noreferrer">${escapeHtml(sidecarName)}</a>`;
    }).join('');
    elements.detail.innerHTML = `
      ${canOpenPrototype ? `<a class="detail-preview" href="${escapeHtml(prototype.entry)}" target="_blank" rel="noreferrer">${largePreview}</a>` : `<div class="detail-preview is-disabled">${largePreview}</div>`}
      <div class="detail-heading"><h2>${escapeHtml(prototype.title)}</h2><p>${escapeHtml(prototype.description)}</p></div>
      <dl class="detail-meta">
        <div><dt>所属项目</dt><dd>${escapeHtml(prototype.project)}</dd></div><div><dt>所属模块</dt><dd>${escapeHtml(prototype.module)}</dd></div>
        <div><dt>原型系统</dt><dd>${escapeHtml(prototype.system || '未归入共享外壳')}</dd></div>
        <div><dt>原型形式</dt><dd>${formLabels[prototype.form]}</dd></div><div><dt>验证层级</dt><dd>${escapeHtml(prototype.validationLevel)}</dd></div>
        <div><dt>主要流程</dt><dd>${escapeHtml(prototype.primaryFlow)}</dd></div><div><dt>版本</dt><dd>${escapeHtml(prototype.version)}</dd></div>
        <div><dt>更新时间</dt><dd>${escapeHtml(prototype.updatedAt)}</dd></div><div><dt>可用状态</dt><dd>${availabilityLabels[prototype.availability]}</dd></div>
      </dl>
      ${relationshipMarkup ? `<section class="detail-relationships" aria-labelledby="related-heading"><h3 id="related-heading">关联原型</h3><ul>${relationshipMarkup}</ul></section>` : ''}
      ${provenanceMarkup ? `<section class="detail-provenance" aria-label="图片来源记录"><span>图片来源记录</span><div>${provenanceMarkup}</div></section>` : ''}
      <div class="detail-actions"><a class="detail-action is-secondary" href="${escapeHtml(prototype.source)}" target="_blank" rel="noreferrer">查看说明</a>${canOpenPrototype ? `<a class="detail-action" href="${escapeHtml(prototype.entry)}" target="_blank" rel="noreferrer">打开原型</a>` : '<span class="detail-action is-disabled" aria-disabled="true">暂不可用</span>'}</div>`;
  };

  const renderList = () => {
    const visiblePrototypes = prototypeRegistry.filter(matchesFilters);
    if (!visiblePrototypes.some((prototype) => prototype.id === state.selectedId)) state.selectedId = visiblePrototypes[0]?.id ?? '';
    elements.visibleCount.textContent = String(visiblePrototypes.length);
    elements.empty.hidden = visiblePrototypes.length > 0;
    elements.list.innerHTML = visiblePrototypes.map((prototype) => `
      <tr class="catalog-row${prototype.id === state.selectedId ? ' is-selected' : ''}" data-prototype-id="${prototype.id}" tabindex="0" aria-selected="${prototype.id === state.selectedId}" aria-label="选择${escapeHtml(prototype.title)}并查看详情">
        <td>${prototype.availability === 'available' && prototype.entry ? `<a class="catalog-preview-link" href="${escapeHtml(prototype.entry)}" target="_blank" rel="noreferrer" aria-label="打开${escapeHtml(prototype.title)}">${previewMarkup(prototype)}</a>` : `<span class="catalog-preview-link is-disabled">${previewMarkup(prototype)}</span>`}</td>
        <td>${prototype.availability === 'available' && prototype.entry ? `<a class="catalog-title-link" href="${escapeHtml(prototype.entry)}" target="_blank" rel="noreferrer">${escapeHtml(prototype.title)}</a>` : `<span class="catalog-title-link is-disabled">${escapeHtml(prototype.title)}</span>`}</td>
        <td>${escapeHtml(prototype.project)}</td><td>${escapeHtml(prototype.module)}</td><td><span class="type-pill">${formLabels[prototype.form]}</span></td>
        <td>${escapeHtml(prototype.version)}</td><td>${escapeHtml(prototype.updatedAt)}</td>
        <td><span class="availability-pill${prototype.availability === 'archived' ? ' is-archived' : ''}">● ${availabilityLabels[prototype.availability]}</span></td>
      </tr>`).join('');
    renderDetail();
  };

  const resetFilters = () => {
    Object.assign(state, { query: '', project: 'all', module: 'all', system: 'all', form: 'all', availability: 'all' });
    elements.search.value = '';
    [elements.project, elements.module, elements.system, elements.form, elements.availability].forEach((select) => { select.value = 'all'; });
    elements.navItems.forEach((button) => button.classList.toggle('is-active', button.dataset.navFilter === 'all'));
    renderList();
  };

  const selectPrototypeRow = (prototypeRow) => {
    state.selectedId = prototypeRow.dataset.prototypeId;
    renderList();
    [...elements.list.querySelectorAll('[data-prototype-id]')]
      .find((candidateRow) => candidateRow.dataset.prototypeId === state.selectedId)
      ?.focus();
  };

  appendOptions(elements.project, uniqueValues('project'));
  appendOptions(elements.module, uniqueValues('module'));
  appendOptions(elements.system, uniqueValues('system').filter(Boolean));
  elements.totalCount.textContent = String(prototypeRegistry.length);
  elements.search.addEventListener('input', (event) => { state.query = event.target.value.trim().toLowerCase(); renderList(); });
  [['project', elements.project], ['module', elements.module], ['system', elements.system], ['form', elements.form], ['availability', elements.availability]].forEach(([key, select]) => {
    select.addEventListener('change', (event) => { state[key] = event.target.value; renderList(); });
  });
  elements.clear.addEventListener('click', resetFilters);
  elements.list.addEventListener('click', (event) => {
    if (event.target.closest('a, button')) return;
    const selectedRow = event.target.closest('[data-prototype-id]');
    if (!selectedRow) return;
    selectPrototypeRow(selectedRow);
  });
  elements.list.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key) || event.target.closest('a, button')) return;
    const selectedRow = event.target.closest('[data-prototype-id]');
    if (!selectedRow) return;
    event.preventDefault();
    selectPrototypeRow(selectedRow);
  });
  elements.navItems.forEach((button) => button.addEventListener('click', () => {
    const filter = button.dataset.navFilter;
    state.form = ['image-state', 'code-native'].includes(filter) ? filter : 'all';
    state.availability = filter === 'available' ? 'available' : 'all';
    elements.form.value = state.form;
    elements.availability.value = state.availability;
    elements.navItems.forEach((candidate) => candidate.classList.toggle('is-active', candidate === button));
    renderList();
  }));
  renderList();
})();
