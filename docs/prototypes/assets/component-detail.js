(() => {
  const formSections = [
    {
      id: 'text-inputs', title: '文本输入', description: '覆盖普通输入、长文本和校验状态。',
      markup: `<div class="variant-grid"><section class="variant-card"><h3>默认输入框</h3><p>适合名称、路径和短文本。</p><label>原型名称<input value="CI/CD 自动修复" /></label></section><section class="variant-card"><h3>错误状态</h3><p>错误信息紧跟字段并说明恢复方式。</p><label>仓库名称<input class="is-error" value="unknown/repo" /></label><div class="error-text">找不到该仓库，请检查名称。</div></section><section class="variant-card"><h3>禁用状态</h3><p>保留当前值，但不可修改。</p><label>任务编号<input value="PRD-128" disabled /></label></section><section class="variant-card"><h3>长文本</h3><p>用于描述和备注。</p><label>说明<textarea rows="4">完成后等待 CI/CD，并根据策略决定是否自动修复。</textarea></label></section></div>`,
    },
    {
      id: 'selects', title: '选择器', description: '不同选项规模与选择模式拆成独立变体。',
      markup: `<div class="variant-grid"><section class="variant-card"><h3>单选</h3><p>选项较少且无需搜索。</p><label>所属模块<select><option>Backlog</option><option>Processes</option><option>Repositories</option></select></label></section><section class="variant-card"><h3>多选</h3><p>已选项目使用 Token 表达。</p><div class="select-preview"><div class="token-row"><span class="token">Backlog ×</span><span class="token">Processes ×</span></div><span class="select-option">Repositories <b>+</b></span></div></section><section class="variant-card"><h3>可搜索选择器</h3><p>适用于选项较多的仓库列表。</p><div class="select-preview"><input placeholder="搜索仓库…" /><span class="select-option is-selected">keda-main <b>✓</b></span><span class="select-option">cloud-platform</span><span class="select-option">iar-console</span></div></section><section class="variant-card"><h3>异步加载</h3><p>远程结果需要显示加载与空状态。</p><div class="select-preview"><input value="front" /><span class="select-option">正在加载仓库… <b>◌</b></span></div></section><section class="variant-card"><h3>级联选择</h3><p>先选项目，再限制模块范围。</p><div class="component-row"><select><option>keda</option></select><span>→</span><select><option>Backlog</option></select></div></section><section class="variant-card"><h3>禁用与空状态</h3><p>未满足前置条件时说明原因。</p><label>模块<select disabled><option>请先选择项目</option></select></label></section></div>`,
    },
    {
      id: 'choices', title: '勾选与开关', description: '区分多选、单选和即时生效控制。',
      markup: `<div class="variant-grid"><section class="variant-card"><h3>Checkbox</h3><p>允许选择多个独立选项。</p><div class="choice-stack"><label><input type="checkbox" checked />显示已归档原型</label><label><input type="checkbox" />只看我维护的原型</label><label><input type="checkbox" disabled />包含不可用原型</label></div></section><section class="variant-card"><h3>Radio</h3><p>同组只能选择一项。</p><div class="choice-stack"><label><input type="radio" name="policy" checked />跟随全局</label><label><input type="radio" name="policy" />强制开启</label><label><input type="radio" name="policy" />强制关闭</label></div></section><section class="variant-card"><h3>Switch</h3><p>适合即时开启或关闭设置。</p><div class="choice-stack"><label><input type="checkbox" role="switch" checked />允许自动修复</label><label><input type="checkbox" role="switch" />显示高级信息</label></div></section><section class="variant-card"><h3>日期与时间</h3><p>使用浏览器原生输入模拟选择。</p><label>更新时间<input type="datetime-local" value="2026-09-16T16:30" /></label></section></div>`,
    },
    {
      id: 'state-matrix', title: '状态矩阵', description: '每个表单组件至少检查这些通用状态。',
      markup: `<table class="state-matrix"><thead><tr><th>状态</th><th>视觉要求</th><th>交互要求</th></tr></thead><tbody><tr><td>默认</td><td>边界清晰、标签可读</td><td>支持鼠标和键盘</td></tr><tr><td>Focus</td><td>蓝色焦点环</td><td>焦点顺序符合页面顺序</td></tr><tr><td>Loading</td><td>保留控件宽度</td><td>避免重复提交</td></tr><tr><td>Error</td><td>红色边界和错误文字</td><td>说明恢复动作</td></tr><tr><td>Disabled</td><td>降低对比度但仍可读</td><td>不可触发事件</td></tr><tr><td>Empty</td><td>说明没有结果</td><td>允许清除筛选或重试</td></tr></tbody></table>`,
    },
  ];
  const makeSection = ({ id, title, description, markup }) => ({ id, title, description, markup });
  const simpleDefinitions = {
    buttons: {
      title: '按钮', category: 'Actions', name: 'Button', description: '操作的层级、尺寸和状态。',
      sections: [
        makeSection({ id: 'hierarchy', title: '层级与语义', description: '一个操作区域只突出一个主要动作。', markup: '<div class="component-row"><span class="ui-button">主要操作</span><span class="ui-button secondary">次要操作</span><span class="ui-button danger">危险操作</span></div>' }),
        makeSection({ id: 'sizes', title: '尺寸', description: '尺寸随页面密度变化，动作层级保持一致。', markup: '<div class="component-row"><span class="ui-button secondary" style="min-height:30px;padding:5px 9px;font-size:11px">紧凑</span><span class="ui-button">默认</span><span class="ui-button" style="min-height:46px;padding:12px 18px;font-size:14px">宽松</span></div>' }),
        makeSection({ id: 'loading-disabled', title: 'Loading 与 Disabled', description: '提交中保留原宽度，并禁用重复触发。', markup: '<div class="component-row"><button class="ui-button" type="button" disabled>保存中…</button><button class="ui-button secondary" type="button" disabled>当前不可用</button></div>' }),
      ],
    },
    badges: {
      title: '状态标签', category: 'Data display', name: 'Badge', description: '用文字和颜色共同表达状态。',
      sections: [
        makeSection({ id: 'semantic-colors', title: '语义颜色', description: '状态文字与颜色一致，避免只靠颜色传达结果。', markup: '<div class="component-row"><span class="ui-badge success">● 已通过</span><span class="ui-badge info">运行中</span><span class="ui-badge warning">等待检查</span><span class="ui-badge danger">失败</span><span class="ui-badge neutral">未开始</span></div>' }),
        makeSection({ id: 'icon-labels', title: '带图标标签', description: '图标只作辅助，标签本身也要能说明状态。', markup: '<div class="component-row"><span class="ui-badge success">✓ 已归档</span><span class="ui-badge warning">◷ 等待 CI</span><span class="ui-badge danger">! 需要处理</span></div>' }),
        makeSection({ id: 'long-labels', title: '长文本边界', description: '长状态保留完整文字并允许自然换行。', markup: '<div class="component-row"><span class="ui-badge warning">等待独立 verifier 返回结论</span><span class="ui-badge danger">观测数据不完整</span></div>' }),
      ],
    },
    cards: {
      title: '卡片', category: 'Containers', name: 'Card', description: '用于组织指标、问题和空状态。',
      sections: [
        makeSection({ id: 'metric-cards', title: '指标卡', description: '数值、名称和口径说明保持在同一视觉组。', markup: '<div class="card-examples"><section class="metric-card"><span>已完成 PRD</span><strong>18</strong><small>最近 30 天</small></section><section class="metric-card"><span>平均端到端耗时</span><strong>3.4h</strong><small>仅统计已完成记录</small></section><section class="metric-card"><span>平均阻塞时间</span><strong>42m</strong><small>占总耗时 20%</small></section></div>' }),
        makeSection({ id: 'issue-card', title: '问题卡', description: '状态、问题摘要与实际动作分开呈现。', markup: '<section class="status-card"><div><span class="ui-badge danger">失败</span><strong>backend / typecheck</strong></div><p>SessionSnapshot 缺少 resume_token 字段</p><span class="text-action">查看详情 →</span></section>' }),
        makeSection({ id: 'empty-card', title: '空状态卡', description: '没有待处理内容时说明原因和当前状态。', markup: '<section class="empty-card"><strong>暂无待处理问题</strong><p>所有检查均已通过。</p></section>' }),
      ],
    },
    table: {
      title: '表格', category: 'Data display', name: 'Table', description: '支持扫描、选择、排序和空状态。',
      sections: [
        makeSection({ id: 'base-table', title: '基础表格', description: '列名和单元格保持简短，状态使用文字标签。', markup: '<div class="table-wrap"><table class="ui-table is-static"><thead><tr><th>名称</th><th>模块</th><th>状态</th></tr></thead><tbody><tr><td>CI/CD 自动修复</td><td>Backlog</td><td><span class="ui-badge success">可用</span></td></tr><tr><td>验收证据浏览</td><td>Backlog</td><td><span class="ui-badge info">验证中</span></td></tr></tbody></table></div>' }),
        makeSection({ id: 'row-selection', title: '行选择', description: '选中行使用背景和焦点轮廓双重提示。', markup: '<div class="table-wrap"><table class="ui-table is-static"><thead><tr><th>名称</th><th>状态</th><th>更新时间</th></tr></thead><tbody><tr><td>生命周期与执行器设置</td><td>可用</td><td>刚刚</td></tr><tr class="is-selected"><td>旧版生命周期矩阵</td><td>已归档</td><td>2026-10-08</td></tr></tbody></table></div>' }),
        makeSection({ id: 'loading-empty', title: 'Loading 与 Empty', description: '加载和无结果状态应留在表格区域内。', markup: '<div class="card-examples"><section class="empty-card"><strong>正在加载记录…</strong><p>列表会在数据返回后显示。</p></section><section class="empty-card"><strong>没有匹配的记录</strong><p>清除筛选后查看全部记录。</p></section></div>' }),
      ],
    },
    feedback: {
      title: '反馈与浮层', category: 'Feedback', name: 'Dialog · Toast', description: '为操作结果和高风险动作提供反馈。',
      sections: [
        makeSection({ id: 'dialog', title: 'Dialog', description: '确认框说明影响，并提供明确的取消路径。', markup: '<section class="dialog"><h2>归档这个原型？</h2><p>归档后仍可在 Hub 的“已归档”筛选中找到。</p><div><button class="ui-button secondary" type="button" disabled>取消</button><button class="ui-button danger" type="button" disabled>确认归档</button></div></section>' }),
        makeSection({ id: 'toast', title: 'Toast', description: '短暂反馈说明已完成的操作，不替代错误说明。', markup: '<div class="toast" role="status" style="position:static;display:inline-block">设置已保存</div>' }),
        makeSection({ id: 'loading-error', title: 'Loading 与错误', description: '处理中和失败都应给出清晰、不同的状态反馈。', markup: '<div class="component-row"><span class="ui-badge info">◌ 正在保存</span><span class="ui-badge danger">保存失败：请重试</span></div>' }),
      ],
    },
  };
  const params = new URLSearchParams(window.location.search);
  const componentKey = params.get('component') || 'forms';
  const title = document.querySelector('#component-title');
  const category = document.querySelector('#component-category');
  const description = document.querySelector('#component-description');
  const componentName = document.querySelector('#component-name');
  const content = document.querySelector('#component-detail-content');
  const detailNav = document.querySelector('#detail-nav');

  const renderSections = (sections) => {
    detailNav.innerHTML = sections.map((section) => `<a href="#${section.id}">${section.title}</a>`).join('');
    content.innerHTML = sections.map((section) => `<section id="${section.id}" class="detail-section"><h2>${section.title}</h2><p>${section.description}</p>${section.markup}</section>`).join('');
  };
  if (componentKey === 'forms') {
    title.textContent = '表单与选择'; category.textContent = 'Inputs'; description.textContent = '按组件类型、选项规模和交互状态拆分表单变体。'; componentName.textContent = 'Input · Select · Choice';
    renderSections(formSections);
  } else {
    const definition = simpleDefinitions[componentKey] || simpleDefinitions.buttons;
    title.textContent = definition.title; category.textContent = definition.category; description.textContent = definition.description; componentName.textContent = definition.name;
    renderSections(definition.sections);
  }
})();
