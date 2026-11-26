(() => {
  const byId = (elementId) => document.getElementById(elementId);
  const escapeText = (textValue) => String(textValue).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
  const flows = [
    {
      id: 'delivery', name: '代码交付 · 当前节点',
      nodes: [
        { id: 'claim', name: '认领 Issue', kind: 'tool', capability: 'issue.claim', input: 'issue.number' },
        { id: 'implement', name: '实现', kind: 'agent', role: 'implementation', preset: 'codex-luna-max' },
        { id: 'checks', name: '执行检查', kind: 'tool', capability: 'checks.run', input: 'implement.commit_sha' },
        { id: 'verify', name: '校验', kind: 'agent', role: 'verifier', preset: 'codex-terra-medium' },
        { id: 'gate', name: '交付门禁', kind: 'tool', capability: 'delivery.check', input: 'verify.evidence' },
        { id: 'review', name: '审核', kind: 'agent', role: 'review', preset: 'codex-terra-medium' },
        { id: 'push', name: '推送代码', kind: 'tool', capability: 'git.push', input: 'review.commit_sha' },
        { id: 'pr', name: 'Draft PR', kind: 'tool', capability: 'github.ensure_pr', input: 'push.commit_sha' },
        { id: 'supervise', name: '监督', kind: 'agent', role: 'supervisor', preset: 'codex-luna-max' },
        { id: 'fix', name: '修复', kind: 'agent', role: 'fix', preset: '继承实现', branch: true },
        { id: 'closeout', name: '收尾', kind: 'agent', role: 'closeout', preset: '继承实现', branch: true },
      ],
    },
    {
      id: 'discussion', name: '讨论与内容生成 · 当前节点',
      nodes: [
        { id: 'pending', name: '待讨论 Issue', kind: 'tool', capability: 'issue.await_discussion', input: 'issue.number' },
        { id: 'debate', name: '辩论', kind: 'agent', role: 'deliberate', preset: 'codebuddy-default' },
        { id: 'human', name: '等待人工标记', kind: 'tool', capability: 'issue.await_label', input: 'debate.issue_number' },
        { id: 'generate', name: '内容生成', kind: 'agent', role: 'content_generation', preset: 'codebuddy-default', targets: ['创建 Issue', '提示词建 Issue', 'Issue→PRD', 'Draft PR 文案'] },
        { id: 'prd', name: 'PRD', kind: 'output', source: 'generate.document' },
        { id: 'body', name: 'Issue 正文', kind: 'output', source: 'generate.issue_body' },
        { id: 'prompt', name: '从提示词创建 Issue', kind: 'tool', capability: 'issue.create_from_prompt', input: 'prompt', branch: true },
      ],
    },
    {
      id: 'ask', name: '独立问答 · kc ask',
      nodes: [{ id: 'planner', name: '独立问答', kind: 'agent', role: 'planner', preset: 'codex-luna-max', input: 'question' }],
    },
    {
      id: 'early', name: '先交付后验证 · 目标流程',
      nodes: [
        { id: 'implement', name: '实现', kind: 'agent', role: 'implementation', preset: 'codex-luna-max' },
        { id: 'pr', name: '交付 Draft PR', kind: 'tool', capability: 'github.ensure_pr', input: 'implement.commit_sha' },
        { id: 'watch', name: '等待 CI / 监控', kind: 'tool', capability: 'github.watch_checks', input: 'pr.head_sha', handler: '代码适配器', checkpoint: { external_run_id: 'ci-run-874', target_commit: 'a81c2f0', cursor: 'check-3', poll_after_seconds: 30 } },
        { id: 'verify', name: '独立校验', kind: 'agent', role: 'verifier', preset: 'codex-terra-medium' },
        { id: 'fix', name: '修复', kind: 'agent', role: 'fix', preset: '继承实现', branch: true },
      ],
    },
  ];
  let activeFlowId = 'all';
  let scenario = 'config';
  let refreshed = false;

  function selectedFlow() {
    const definition = structuredClone(flows.find((flow) => flow.id === activeFlowId));
    if (scenario === 'cli-added' && definition.id === 'delivery') {
      definition.nodes.splice(3, 0, { id: 'security_review', name: '安全评审', kind: 'agent', preset: 'codex-terra-medium', input: 'implement.commit_sha' });
    }
    return definition;
  }

  function showTab(tabName) {
    for (const viewName of ['definition', 'run']) {
      byId(`${viewName}-panel`).hidden = viewName !== tabName;
      byId(`${viewName}-tab`).setAttribute('aria-selected', String(viewName === tabName));
    }
  }

  const linksByFlow = {
    delivery: [['claim', 'implement'], ['implement', 'checks'], ['checks', 'verify'], ['verify', 'gate'], ['gate', 'review'], ['review', 'push'], ['push', 'pr'], ['pr', 'supervise'], ['checks', 'fix', '失败'], ['fix', 'checks', '复查'], ['gate', 'closeout', '材料缺失'], ['closeout', 'gate', '复查']],
    discussion: [['pending', 'debate'], ['debate', 'human'], ['human', 'generate', '人工标记后'], ['prompt', 'generate'], ['generate', 'prd', '整理需求'], ['generate', 'body', '创建 Issue']],
    ask: [],
    early: [['implement', 'pr'], ['pr', 'watch'], ['watch', 'verify'], ['verify', 'fix', '失败'], ['fix', 'watch', '复查']],
  };
  const originalNodeIds = {
    delivery: { issue: 'claim', implement: 'implement', check: 'checks', verify: 'verify', gate: 'gate', review: 'review', pr: 'pr', supervise: 'supervise', fix: 'fix', closeout: 'closeout' },
    discussion: { issue: 'pending', debate: 'debate', prompt: 'prompt', generate: 'generate', prd: 'prd', body: 'body' },
  };
  let previousTrigger = null;

  function renderDefinition() {
    byId('workflow-select').innerHTML = '<option value="all">现有全部流程</option>' + flows.map((flow) => `<option value="${flow.id}">${flow.name}</option>`).join('');
    byId('workflow-select').value = activeFlowId;
    byId('actual-graphs').hidden = activeFlowId !== 'all';
    byId('custom-graph').hidden = activeFlowId === 'all';
    if (activeFlowId === 'all') {
      byId('actual-graphs').innerHTML = window.KEDA_ACTUAL_WORKFLOW_GRAPHS;
      const sections = [...byId('actual-graphs').querySelectorAll('section')];
      for (const [sectionIndex, section] of sections.entries()) {
        const flowId = sectionIndex === 0 ? 'delivery' : 'discussion';
        for (const wrapper of section.querySelectorAll('.react-flow__node')) {
          const nodeId = originalNodeIds[flowId][wrapper.dataset.id];
          const target = wrapper.querySelector('button') || wrapper;
          target.dataset.prototypeNode = `${flowId}.${nodeId}`;
          target.setAttribute('aria-label', `查看${flows.find((flow) => flow.id === flowId).nodes.find((node) => node.id === nodeId).name}节点详情`);
          if (target === wrapper) {
            target.setAttribute('role', 'button');
            target.tabIndex = 0;
            target.style.cursor = 'pointer';
            target.style.pointerEvents = 'auto';
          }
        }
      }
      const planner = [...byId('actual-graphs').querySelectorAll('button')].find((button) => button.textContent.includes('kc ask'));
      planner.dataset.prototypeNode = 'ask.planner';
      return;
    }

    const definition = selectedFlow();
    byId('graph-title').textContent = definition.name;
    const mainNodes = definition.nodes.filter((node) => !node.branch && node.id !== 'body');
    const placements = new Map(mainNodes.map((node, nodeIndex) => [node.id, { x: nodeIndex * 180 + 12, y: 30 }]));
    if (definition.id === 'discussion') placements.set('body', { x: 732, y: 165 });
    for (const node of definition.nodes.filter((node) => node.branch)) {
      const anchorId = node.id === 'closeout' ? 'gate' : definition.id === 'discussion' ? 'debate' : placements.has('checks') ? 'checks' : 'verify';
      placements.set(node.id, { x: placements.get(anchorId).x + (node.id === 'prompt' ? 0 : 55), y: 165 });
    }
    const links = structuredClone(linksByFlow[definition.id]);
    if (scenario === 'cli-added' && definition.id === 'delivery') {
      links[2] = ['checks', 'security_review'];
      links.push(['security_review', 'verify']);
    }
    const edgeMarkup = links.map(([sourceId, targetId, label = '']) => {
      const source = placements.get(sourceId);
      const target = placements.get(targetId);
      const startX = source.x + 150;
      const startY = source.y + 27;
      const endX = target.x;
      const endY = target.y + 27;
      return `<path d="M${startX},${startY} C${startX + 50},${startY} ${endX - 50},${endY} ${endX},${endY}" marker-end="url(#workflow-arrow)"/><text x="${(startX + endX) / 2}" y="${(startY + endY) / 2 - 8}" text-anchor="middle">${escapeText(label)}</text>`;
    }).join('');
    const nodeMarkup = definition.nodes.map((node) => {
      const position = placements.get(node.id);
      return `<button class="workflow-node" type="button" data-prototype-node="${definition.id}.${node.id}" data-kind="${node.kind}" style="left:${position.x}px;top:${position.y}px"><strong>${escapeText(node.name)} ›</strong><small>${escapeText(node.preset || node.capability || '产物引用')}</small></button>`;
    }).join('');
    byId('graph').style.width = `${Math.max(...[...placements.values()].map((position) => position.x)) + 175}px`;
    byId('graph').innerHTML = `<svg width="100%" height="270" aria-hidden="true"><defs><marker id="workflow-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><polygon points="0,0 10,5 0,10" fill="#afadb7"/></marker></defs>${edgeMarkup}</svg>${nodeMarkup}`;
  }

  function renderRun() {
    const unknown = scenario === 'unknown';
    const monitoring = scenario === 'monitor' && !refreshed;
    byId('delivery-status').textContent = unknown ? '工具结果待核对' : '已交付 Draft PR';
    byId('delivery-status').className = `status ${unknown ? '' : 'success'}`;
    byId('validation-status').textContent = monitoring ? '监控等待中' : refreshed && !unknown ? '验证已通过 · a81c2f0' : '待执行';
    byId('worker-status').textContent = monitoring ? '检查位置已保存，等待下次调度' : refreshed && !unknown ? '后台结果已同步' : '后续任务已保存，等待 KC 调度';
    byId('run-rows').innerHTML = `<tr><td>实现</td><td>1</td><td>已完成</td><td class="font-mono">a81c2f0</td></tr><tr><td>交付 Draft PR</td><td>1</td><td>${unknown ? '待核对' : '已完成'}</td><td>${unknown ? '响应丢失，等待远端查询' : '操作已确认，复用同一 PR'}</td></tr><tr><td>CI 监控</td><td>1</td><td>${monitoring ? 'waiting' : refreshed && !unknown ? '已完成' : '待执行'}</td><td>目标 ci-run-874 · 游标 check-3 · 30 秒后查询</td></tr><tr><td>校验</td><td>1</td><td>${refreshed && !unknown ? '已通过' : '待执行'}</td><td>验证目标 a81c2f0</td></tr>`;
    byId('run-note').textContent = unknown ? '结果未知与正常等待分别记录。当前状态等待后台对账。' : monitoring ? '后台恢复继续查询 ci-run-874，沿用已保存的检查位置和等待预算。' : '交付和验证分别记录，页面从 CLI 与后台调度产生的事实读取状态。';
  }

  byId('definition-tab').addEventListener('click', () => showTab('definition'));
  byId('run-tab').addEventListener('click', () => { renderRun(); showTab('run'); });
  byId('workflow-select').addEventListener('change', (event) => {
    activeFlowId = event.target.value;
    renderDefinition();
  });
  byId('definition-panel').addEventListener('click', (event) => {
    const target = event.target.closest('[data-prototype-node]');
    if (!target) return;
    const [flowId, nodeId] = target.dataset.prototypeNode.split('.');
    const definition = activeFlowId === 'all' ? flows.find((flow) => flow.id === flowId) : selectedFlow();
    const node = structuredClone(definition.nodes.find((node) => node.id === nodeId));
    if (activeFlowId === 'all' && node.kind === 'agent') {
      const displayedPreset = target.querySelector('span')?.textContent.trim();
      if (displayedPreset) node.preset = displayedPreset;
    }
    previousTrigger = target;
    byId('selected-title').textContent = `${node.name} · 节点详情`;
    byId('node-description').textContent = node.kind === 'agent' ? 'Agent 配置由 CLI 定义，运行记录冻结实际模型和指令。' : node.kind === 'output' ? '产物端点引用上游输出，对应写入由工具记录。' : '代码能力可执行检查、外部调用或持续监控。';
    byId('node-definition').textContent = JSON.stringify(node, null, 2);
    byId('recovery-note').textContent = node.checkpoint ? '监控返回 waiting，保存目标、游标和下次检查时间。重启接着查询同一对象。' : node.kind === 'agent' ? '记录会话与成果前提，重试和新一轮修复分别留痕。' : node.kind === 'output' ? '保留来源节点、输出字段与产物版本。' : '动作执行前记账。结果未知先核对，能确认时接续，否则保留待核对。';
    byId('node-dialog').showModal();
  });
  byId('definition-panel').addEventListener('keydown', (event) => {
    const target = event.target.closest('[data-prototype-node]');
    if (target?.tagName === 'DIV' && ['Enter', ' '].includes(event.key)) {
      event.preventDefault();
      target.click();
    }
  });
  byId('close-node').addEventListener('click', () => byId('node-dialog').close());
  byId('node-dialog').addEventListener('close', () => previousTrigger?.focus());
  byId('scenario').addEventListener('change', (event) => {
    scenario = event.target.value;
    refreshed = false;
    activeFlowId = scenario === 'config' ? 'all' : scenario === 'cli-added' ? 'delivery' : 'early';
    renderDefinition();
    renderRun();
    showTab(['config', 'cli-added'].includes(scenario) ? 'definition' : 'run');
  });
  byId('refresh').addEventListener('click', () => { refreshed = true; renderRun(); });
  document.addEventListener('prototype:reset', () => {
    activeFlowId = 'all';
    scenario = 'config';
    refreshed = false;
    byId('scenario').value = 'config';
    if (byId('node-dialog').open) byId('node-dialog').close();
    renderDefinition();
    showTab('definition');
  });
  renderDefinition();
  renderRun();
})();
