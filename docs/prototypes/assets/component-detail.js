(() => {
  const formSections = [
    {
      id: 'text-inputs', title: '文本输入', description: '覆盖普通输入、长文本和校验状态。',
      markup: `<div class="variant-grid"><section class="variant-card"><h3>默认输入框</h3><p>适合名称、路径和短文本。</p><label>原型名称<input value="CI/CD 自动修复" /></label></section><section class="variant-card"><h3>错误状态</h3><p>错误信息紧跟字段并说明恢复方式。</p><label>仓库名称<input class="is-error" value="unknown/repo" /></label><div class="error-text">找不到该仓库，请检查名称。</div></section><section class="variant-card"><h3>禁用状态</h3><p>保留当前值，但不可修改。</p><label>任务编号<input value="PRD-128" disabled /></label></section><section class="variant-card"><h3>长文本</h3><p>用于描述和备注。</p><label>说明<textarea rows="4">完成后等待 CI/CD，并根据策略决定是否自动修复。</textarea></label></section></div>`,
    },
    {
      id: 'selects', title: '选择器', description: '不同选项规模与选择模式拆成独立变体。',
      markup: `<div class="variant-grid"><section class="variant-card"><h3>单选</h3><p>选项较少且无需搜索。</p><label>所属模块<select><option>Roadmap</option><option>Processes</option><option>Repositories</option></select></label></section><section class="variant-card"><h3>多选</h3><p>已选项目使用 Token 表达。</p><div class="select-preview"><div class="token-row"><span class="token">Roadmap ×</span><span class="token">Processes ×</span></div><span class="select-option">Repositories <b>+</b></span></div></section><section class="variant-card"><h3>可搜索选择器</h3><p>适用于选项较多的仓库列表。</p><div class="select-preview"><input placeholder="搜索仓库…" /><span class="select-option is-selected">keda-main <b>✓</b></span><span class="select-option">cloud-platform</span><span class="select-option">iar-console</span></div></section><section class="variant-card"><h3>异步加载</h3><p>远程结果需要显示加载与空状态。</p><div class="select-preview"><input value="front" /><span class="select-option">正在加载仓库… <b>◌</b></span></div></section><section class="variant-card"><h3>级联选择</h3><p>先选项目，再限制模块范围。</p><div class="component-row"><select><option>keda</option></select><span>→</span><select><option>Roadmap</option></select></div></section><section class="variant-card"><h3>禁用与空状态</h3><p>未满足前置条件时说明原因。</p><label>模块<select disabled><option>请先选择项目</option></select></label></section></div>`,
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
  const simpleDefinitions = {
    buttons: ['按钮', 'Actions', 'Button', '操作的层级、尺寸和状态。', ['层级与语义', '尺寸', 'Loading 与 Disabled']],
    badges: ['状态标签', 'Data display', 'Badge', '用文字和颜色共同表达状态。', ['语义颜色', '带图标标签', '长文本边界']],
    cards: ['卡片', 'Containers', 'Card', '用于组织指标、问题和空状态。', ['指标卡', '问题卡', '空状态卡']],
    table: ['表格', 'Data display', 'Table', '支持扫描、选择、排序和空状态。', ['基础表格', '行选择', 'Loading 与 Empty']],
    feedback: ['反馈与浮层', 'Feedback', 'Dialog · Toast', '为操作结果和高风险动作提供反馈。', ['Dialog', 'Toast', 'Loading 与错误']],
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
    title.textContent = definition[0]; category.textContent = definition[1]; description.textContent = definition[3]; componentName.textContent = definition[2];
    renderSections(definition[4].map((sectionTitle, index) => ({ id: `variant-${index + 1}`, title: sectionTitle, description: '该分组用于继续补充组件变体与状态。', markup: `<div class="variant-card"><div class="component-row"><span class="ui-badge info">示例</span><strong>${sectionTitle}</strong></div></div>` })));
  }
})();
